"""Data-management routes: usage, backups, vacuum, prune (Settings → Data)."""

import os
import sqlite3
import stat
import tempfile
from datetime import timedelta

from flask.testing import FlaskClient
from sqlalchemy import select, update

from jalebi import data_mgmt, db, repos, tasks
from jalebi.config import Config


def _repo(session, pat="p") -> int:
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://example.com/owner/repo.git",
        pat_name=pat,
    )
    return row.id


def test_usage_reports_sizes_and_counts(client: FlaskClient, session, config: Config) -> None:
    repo_id = _repo(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_id, prompt="do it")
    assert task.id > 0
    body = client.get("/api/data/usage").get_json()
    assert set(body["sizes"]) >= {"db", "mirrors", "worktrees", "artifacts", "logs", "backups"}
    assert body["counts"]["tasks"] == 1
    assert body["tasks_by_status"].get("queued") == 1


def test_backup_round_trip(client: FlaskClient, config: Config) -> None:
    assert client.get("/api/data/backups").get_json() == []
    resp = client.post("/api/data/backups")
    assert resp.status_code == 201
    info = resp.get_json()
    assert info["name"].startswith("data-") and info["name"].endswith(".db")
    assert info["size"] > 0
    assert len(client.get("/api/data/backups").get_json()) == 1
    # The backup is a valid SQLite DB.
    dl = client.get(f"/api/data/backups/{info['name']}/download")
    assert dl.status_code == 200
    assert dl.data[:16] == b"SQLite format 3\x00"
    # Unknown names 404, traversal refused.
    assert client.get("/api/data/backups/nope.db/download").status_code == 404
    assert client.get("/api/data/backups/../app/download").status_code == 404
    assert client.delete(f"/api/data/backups/{info['name']}").status_code == 200
    assert client.get("/api/data/backups").get_json() == []


def test_vacuum_reports_sizes(client: FlaskClient) -> None:
    body = client.post("/api/data/vacuum").get_json()
    assert body["before"] > 0
    assert body["after"] > 0


def test_prune_dry_run_then_execute(
    client: FlaskClient, session, config: Config
) -> None:
    repo_id = _repo(session)
    old = tasks.create_task(session, type_="freeform", repo_id=repo_id, prompt="old")
    # Age the task past the cutoff without touching production clocks.
    session.execute(
        update(db.Task)
        .where(db.Task.id == old.id)
        .values(status="done", updated_at=db.now() - timedelta(days=60))
    )
    session.commit()
    new = tasks.create_task(session, type_="freeform", repo_id=repo_id, prompt="new")
    old_id, new_id = old.id, new.id

    body = {"older_than_days": 30, "scopes": ["tasks"], "dry_run": True}
    preview = client.post("/api/data/prune", json=body).get_json()
    assert preview["dry_run"] is True
    assert preview["preview"]["tasks"]["task_ids"] == [old_id]

    # Execute requires the confirm word; anything else previews.
    again = client.post(
        "/api/data/prune", json={**body, "dry_run": False}
    ).get_json()
    assert again["dry_run"] is True
    done = client.post(
        "/api/data/prune", json={**body, "dry_run": False, "confirm": "DELETE"}
    ).get_json()
    assert done["dry_run"] is False
    assert done["removed"]["tasks"] == 1

    session.expire_all()
    assert tasks.get_task(session, old_id) is None
    assert tasks.get_task(session, new_id) is not None

    # Bad input is a 400, never a partial prune.
    assert client.post("/api/data/prune", json={"older_than_days": 0}).status_code == 400
    assert (
        client.post("/api/data/prune", json={"scopes": ["everything"]}).status_code == 400
    )


def test_prune_orphan_worktree_dirs(client: FlaskClient, session, config: Config) -> None:
    orphan = config.data_dir / "ws" / "task-999999"
    orphan.mkdir(parents=True)
    (orphan / "note.txt").write_text("stale\n")
    preview = client.post(
        "/api/data/prune", json={"older_than_days": 30, "scopes": ["orphans"]}
    ).get_json()
    assert 999999 in preview["preview"]["orphan_worktrees"]
    done = client.post(
        "/api/data/prune",
        json={"older_than_days": 30, "scopes": ["orphans"], "dry_run": False, "confirm": "DELETE"},
    ).get_json()
    assert done["removed"]["orphan_worktrees"] == 1
    assert not orphan.exists()


def _age_task(session, task_id: int, days: int = 60) -> None:
    session.execute(
        update(db.Task)
        .where(db.Task.id == task_id)
        .values(status="done", updated_at=db.now() - timedelta(days=days))
    )
    session.commit()


def test_prune_task_with_checkrun_row(client: FlaskClient, session, config: Config) -> None:
    """Pruning a task that reported a commit status must not IntegrityError."""
    repo_id = _repo(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_id, prompt="old")
    run = db.Run(task_id=task.id, seq=1, status="done", started_at=db.now(), finished_at=db.now())
    session.add(run)
    session.flush()
    session.add(
        db.CheckRun(
            task_id=task.id,
            run_id=run.id,
            repo_id=repo_id,
            head_sha="abc123",
            name="jalebi",
            status="completed",
            conclusion="success",
        )
    )
    session.commit()
    old_id = task.id
    _age_task(session, old_id)

    done = client.post(
        "/api/data/prune",
        json={"older_than_days": 30, "scopes": ["tasks"], "dry_run": False, "confirm": "DELETE"},
    ).get_json()
    assert done["dry_run"] is False
    assert done["removed"]["tasks"] == 1

    session.expire_all()
    assert tasks.get_task(session, old_id) is None
    assert session.execute(select(db.CheckRun).where(db.CheckRun.task_id == old_id)).first() is None


def test_prune_deliveries_removes_screening_worktrees(
    client: FlaskClient, session, config: Config
) -> None:
    repo_id = _repo(session)
    session.add(
        db.EventDelivery(
            github_delivery_id="dl-1",
            event="pull_request",
            payload_json="{}",
            received_at=db.now() - timedelta(days=60),
        )
    )
    screen = db.Screening(repo_id=repo_id, name="s", system_prompt="p")
    session.add(screen)
    session.flush()
    run = db.ScreeningRun(
        screening_id=screen.id,
        status="done",
        started_at=db.now() - timedelta(days=60),
        finished_at=db.now() - timedelta(days=60),
    )
    session.add(run)
    session.flush()
    run_id = run.id
    ws = config.data_dir / "ws" / f"screen-{run_id}"
    ws.mkdir(parents=True)
    (ws / "notes.md").write_text("audit\n")
    session.commit()

    preview = client.post(
        "/api/data/prune", json={"older_than_days": 30, "scopes": ["deliveries"]}
    ).get_json()
    assert preview["preview"]["deliveries"] == 1
    assert preview["preview"]["screening_runs"] == 1

    done = client.post(
        "/api/data/prune",
        json={
            "older_than_days": 30,
            "scopes": ["deliveries"],
            "dry_run": False,
            "confirm": "DELETE",
        },
    ).get_json()
    assert done["removed"]["deliveries"] == 1
    assert done["removed"]["screening_runs"] == 1
    assert not ws.exists()
    session.expire_all()
    assert session.get(db.ScreeningRun, run_id) is None


def test_prune_logs_and_orphan_artifacts(client: FlaskClient, session, config: Config) -> None:
    logs = config.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    old_log = logs / "server.log.1"
    old_log.write_text("old\n")
    os.utime(old_log, (0, 0))  # epoch mtime → older than any cutoff
    orphan_art = config.data_dir / "artifacts" / "424242"
    orphan_art.mkdir(parents=True)
    (orphan_art / "out.txt").write_text("stale\n")

    preview = client.post(
        "/api/data/prune", json={"older_than_days": 30, "scopes": ["logs", "orphans"]}
    ).get_json()
    assert preview["preview"]["old_logs"] == 1
    assert 424242 in preview["preview"]["orphan_artifacts"]

    done = client.post(
        "/api/data/prune",
        json={
            "older_than_days": 30,
            "scopes": ["logs", "orphans"],
            "dry_run": False,
            "confirm": "DELETE",
        },
    ).get_json()
    assert done["removed"]["logs"] == 1
    assert done["removed"]["orphan_artifacts"] == 1
    assert not old_log.exists()
    assert not orphan_art.exists()


def test_backup_is_0600_and_usage_counts_wal(client: FlaskClient, config: Config) -> None:
    """Backups hold plaintext secrets → 0600; usage sizes the WAL/SHM too."""
    info = client.post("/api/data/backups").get_json()
    mode = stat.S_IMODE((config.data_dir / "backups" / info["name"]).stat().st_mode)
    assert mode == 0o600
    # A WAL sidecar must be included in the reported db size.
    wal = config.data_dir / "data.db-wal"
    wal.write_bytes(b"x" * 4096)
    try:
        usage = client.get("/api/data/usage").get_json()
        assert usage["sizes"]["db"] >= 4096
    finally:
        wal.unlink()


def test_backup_is_consistent_snapshot(client: FlaskClient, config: Config) -> None:
    """The backup opens and carries the same settings rows as the live DB."""
    client.post("/api/settings", json={"key": "concurrency", "value": 5})
    info = client.post("/api/data/backups").get_json()
    live = sqlite3.connect(str(config.data_dir / "data.db"))
    try:
        live_rows = dict(live.execute("SELECT key, value FROM settings").fetchall())
    finally:
        live.close()
    dl = client.get(f"/api/data/backups/{info['name']}/download")
    fd, tmp = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dl.data)
        snap = sqlite3.connect(tmp)
        try:
            snap_rows = dict(snap.execute("SELECT key, value FROM settings").fetchall())
        finally:
            snap.close()
    finally:
        os.unlink(tmp)
    assert snap_rows == live_rows
    assert live_rows.get("concurrency") == "5"


def test_restore_dry_run_then_execute(client: FlaskClient, session) -> None:
    """Dry run touches nothing; execute swaps the DB back (safety first)."""
    client.post("/api/settings", json={"key": "concurrency", "value": 7})
    backup_a = client.post("/api/data/backups").get_json()
    client.post("/api/settings", json={"key": "concurrency", "value": 2})

    dry = client.post(
        f"/api/data/backups/{backup_a['name']}/restore", json={"dry_run": True}
    ).get_json()
    assert dry["dry_run"] is True
    assert dry["preview"]["integrity_ok"] is True
    assert dry["preview"]["busy_tasks"] == 0
    assert client.get("/api/settings").get_json()["concurrency"] == 2

    # No confirm → still a preview.
    again = client.post(
        f"/api/data/backups/{backup_a['name']}/restore", json={"dry_run": False}
    ).get_json()
    assert again["dry_run"] is True

    done = client.post(
        f"/api/data/backups/{backup_a['name']}/restore",
        json={"dry_run": False, "confirm": "RESTORE"},
    ).get_json()
    assert done["dry_run"] is False
    assert done["restored"] == backup_a["name"]
    assert done["safety_backup"].endswith(".db")
    assert done["safety_backup"] != backup_a["name"]
    assert client.get("/api/settings").get_json()["concurrency"] == 7
    names = [b["name"] for b in client.get("/api/data/backups").get_json()]
    assert done["safety_backup"] in names


def test_restore_refuses_when_busy_and_unknown(client: FlaskClient, session) -> None:
    repo_id = _repo(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_id, prompt="busy")
    assert task.status == "queued"
    backup = client.post("/api/data/backups").get_json()

    # Dry run still reports instead of refusing.
    dry = client.post(
        f"/api/data/backups/{backup['name']}/restore", json={"dry_run": True}
    ).get_json()
    assert dry["dry_run"] is True
    assert dry["preview"]["busy_tasks"] == 1

    resp = client.post(
        f"/api/data/backups/{backup['name']}/restore",
        json={"dry_run": False, "confirm": "RESTORE"},
    )
    assert resp.status_code == 409

    assert client.get("/api/data/backups/nope.db/restore").status_code == 404
    resp = client.post("/api/data/backups/nope.db/restore", json={"dry_run": False})
    assert resp.status_code == 404


def test_restore_refuses_when_screening_running(
    client: FlaskClient, session
) -> None:
    """Restore is refused (409) while a screening run is queued/running (M3)."""
    repo_id = _repo(session)
    screen = db.Screening(repo_id=repo_id, name="s", system_prompt="p")
    session.add(screen)
    session.flush()
    session.add(
        db.ScreeningRun(screening_id=screen.id, head_sha="abc", status="running")
    )
    session.commit()
    backup = client.post("/api/data/backups").get_json()

    dry = client.post(
        f"/api/data/backups/{backup['name']}/restore", json={"dry_run": True}
    ).get_json()
    assert dry["dry_run"] is True
    assert dry["preview"]["busy_screenings"] == 1

    resp = client.post(
        f"/api/data/backups/{backup['name']}/restore",
        json={"dry_run": False, "confirm": "RESTORE"},
    )
    assert resp.status_code == 409


def test_vacuum_busy_returns_409(client: FlaskClient, monkeypatch) -> None:
    """A locked DB maps vacuum to 409 (retry when idle), not 500 (M4)."""

    def _locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", _locked)
    resp = client.post("/api/data/vacuum")
    assert resp.status_code == 409
    assert "idle" in resp.get_json()["error"]


def test_prune_preview_counts_legacy_null_run_events(
    client: FlaskClient, session
) -> None:
    """Preview counts TaskEvent by task_id so legacy run_id-NULL rows agree
    with what execute deletes (L8)."""
    repo_id = _repo(session)
    old = tasks.create_task(session, type_="freeform", repo_id=repo_id, prompt="old")
    run = db.Run(
        task_id=old.id, seq=1, status="done",
        started_at=db.now(), finished_at=db.now(),
    )
    session.add(run)
    session.flush()
    session.add(db.TaskEvent(task_id=old.id, run_id=run.id, seq=1, payload_json="{}"))
    session.add(db.TaskEvent(task_id=old.id, run_id=None, seq=2, payload_json="{}"))
    session.commit()
    _age_task(session, old.id)
    preview = client.post(
        "/api/data/prune", json={"older_than_days": 30, "scopes": ["tasks"]}
    ).get_json()["preview"]
    assert preview["task_events"] == 2


def test_chunked_slices_lists() -> None:
    """The prune IN-chunk helper keeps every statement under the variable
    limit while preserving order and membership (M2)."""
    assert list(data_mgmt._chunked([])) == []
    assert list(data_mgmt._chunked([1, 2, 3], size=2)) == [[1, 2], [3]]
    ids = list(range(1200))
    chunks = list(data_mgmt._chunked(ids))
    assert len(chunks) == 3
    assert all(len(c) <= 500 for c in chunks)
    assert [i for c in chunks for i in c] == ids
