import sqlite3
import subprocess
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from jalebi import db, notifications, repos, secrets, settings, tasks
from jalebi.adapters.types import AgentEvent
from jalebi.db import MIGRATIONS_DIR, Notification

FULL_NAME = "owner/repo"


def _git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class FakeProc:
    def __init__(self) -> None:
        self.killed = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.killed = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        return 0


class FakeHandle:
    def __init__(self, events, session_id: str = "ses_fake"):
        self._events = list(events)
        self.session_id = session_id
        self.proc = FakeProc()

    def events(self):
        yield from self._events


def _install_adapter(monkeypatch, handle) -> None:
    class FakeAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            return handle

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: FakeAdapter())


@pytest.fixture
def git_remote(tmp_path) -> str:
    remote = tmp_path / "remote.git"
    src = tmp_path / "src"
    _git(["init", "--bare", str(remote)])
    _git(["init", str(src)])
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    (src / "file.txt").write_text("hello\n")
    _git(["-C", str(src), "add", "file.txt"])
    _git(["-C", str(src), "commit", "-m", "initial"])
    _git(["-C", str(src), "branch", "-M", "main"])
    _git(["-C", str(src), "remote", "add", "origin", str(remote)])
    _git(["-C", str(src), "push", "-u", "origin", "main"])
    _git(["-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"])
    return str(remote)


@pytest.fixture
def repo_row(session, git_remote, app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")
    row, _ = repos.upsert_repo(
        session, full_name=FULL_NAME, default_branch="main", clone_url=git_remote, pat_name="test"
    )
    return row


@pytest.fixture
def q(app):
    return app.config["JALEBI_QUEUE"]


def test_migration_applies_and_downgrades_cleanly() -> None:
    """Alembic upgrade head applies on a fresh DB and downgrade drops the notifications table."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = f.name
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        command.upgrade(cfg, "head")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(notifications)")
        cols = {r[1]: r[2] for r in cur.fetchall()}
        assert cols == {
            "id": "INTEGER",
            "task_id": "INTEGER",
            "run_id": "INTEGER",
            "kind": "TEXT",
            "title": "TEXT",
            "body": "TEXT",
            "read_at": "DATETIME",
            "created_at": "DATETIME",
        }
        conn.close()

        # Downgrade to down_revision f5a6b7c8d9e0
        command.downgrade(cfg, "f5a6b7c8d9e0")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notifications'")
        assert cur.fetchone() is None
        conn.close()


def test_notify_dedup(session, repo_row) -> None:
    """Second identical unread insert is skipped; after mark_read a new one inserts."""
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="dedup test")

    # 1. First insert succeeds
    n1 = notifications.notify(
        session,
        task_id=task.id,
        run_id=None,
        kind="task_done",
        title="Task #1 done",
        body="freeform task in owner/repo",
    )
    assert n1 is not None
    assert n1.id is not None
    assert n1.read_at is None

    # 2. Second insert with same (task_id, kind) while unread is skipped
    n2 = notifications.notify(
        session,
        task_id=task.id,
        run_id=None,
        kind="task_done",
        title="Task #1 done retry",
        body="freeform task in owner/repo",
    )
    assert n2 is None

    # Distinct kind inserts fine
    n_input = notifications.notify(
        session,
        task_id=task.id,
        run_id=None,
        kind="needs_input",
        title="Task #1 needs input",
    )
    assert n_input is not None

    # 3. Mark the first notification read
    marked = notifications.mark_read(session, n1.id)
    assert marked is not None
    assert marked.read_at is not None

    # 4. Now a new unread notification of kind task_done can be inserted
    n3 = notifications.notify(
        session,
        task_id=task.id,
        run_id=None,
        kind="task_done",
        title="Task #1 done again",
        body="freeform task in owner/repo",
    )
    assert n3 is not None
    assert n3.id != n1.id


def test_prune_keeps_newest_500(session, repo_row) -> None:
    """Table is pruned to the newest 500 rows."""
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="prune test")

    # Create 505 notifications directly (marked as read so dedup doesn't skip)
    for i in range(1, 506):
        row = Notification(
            task_id=task.id,
            run_id=None,
            kind="task_done",
            title=f"Notif {i}",
            body="body",
            read_at=db.now(),
        )
        session.add(row)
    session.commit()

    total_before = len(session.execute(select(Notification)).scalars().all())
    assert total_before == 505

    # Trigger prune directly or through notify
    deleted = notifications.prune_notifications(session, limit=500)
    assert deleted == 5

    remaining = list(
        session.execute(select(Notification).order_by(Notification.id.asc())).scalars().all()
    )
    assert len(remaining) == 500
    # Oldest 5 (ids 1..5) should be gone, remaining are 6..505
    assert remaining[0].title == "Notif 6"
    assert remaining[-1].title == "Notif 505"


def test_notification_routes_and_counts(client, session, repo_row) -> None:
    """Routes return correct shapes, pagination/filtering, mark read, and unread counts."""
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="route test")

    # Initial unread count is 0
    resp = client.get("/api/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.get_json() == {"unread": 0}

    # Add notifications
    n1 = notifications.notify(
        session, task.id, None, "task_done", "Task #1 done", "freeform in owner/repo"
    )
    n2 = notifications.notify(
        session, task.id, None, "needs_input", "Task #1 needs approval", "freeform in owner/repo"
    )

    # Unread count is 2
    resp = client.get("/api/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.get_json() == {"unread": 2}

    # List all
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 2
    # Verify shape
    first = data[0]
    assert first["id"] == n2.id
    assert first["task_id"] == task.id
    assert first["kind"] == "needs_input"
    assert first["title"] == "Task #1 needs approval"
    assert first["repo_id"] == repo_row.id
    assert first["type"] == "freeform"
    assert first["status"] == "queued"
    assert first["read_at"] is None
    assert first["created_at"] is not None

    # Test limit param
    resp = client.get("/api/notifications?limit=1")
    assert resp.status_code == 200
    assert len(resp.get_json()) == 1

    # Mark n2 as read
    resp = client.post(f"/api/notifications/{n2.id}/read")
    assert resp.status_code == 200
    marked_row = resp.get_json()
    assert marked_row["id"] == n2.id
    assert marked_row["read_at"] is not None

    # 404 on nonexistent notification
    resp = client.post("/api/notifications/999999/read")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "notification not found"}

    # Unread count is now 1
    resp = client.get("/api/notifications/unread-count")
    assert resp.get_json() == {"unread": 1}

    # List with unread_only=true returns only n1
    resp = client.get("/api/notifications?unread_only=true")
    assert resp.status_code == 200
    unread_data = resp.get_json()
    assert len(unread_data) == 1
    assert unread_data[0]["id"] == n1.id

    # Mark all read
    resp = client.post("/api/notifications/read-all")
    assert resp.status_code == 200
    assert resp.get_json() == {"marked": 1}

    # Unread count is 0
    resp = client.get("/api/notifications/unread-count")
    assert resp.get_json() == {"unread": 0}


def test_notification_dict_omits_task_gracefully(session) -> None:
    """When a task row is missing/deleted, task fields are omitted gracefully."""
    notif = Notification(
        id=9999,
        task_id=8888,
        run_id=None,
        kind="task_done",
        title="Ghost task done",
        body=None,
        read_at=None,
        created_at=db.now(),
    )
    d = notifications.notification_to_dict(notif, task=None)
    assert d["id"] == 9999
    assert d["task_id"] == 8888
    assert d["title"] == "Ghost task done"
    assert "repo_id" not in d
    assert "type" not in d
    assert "status" not in d
    assert "pr_number" not in d


def test_queue_run_done_produces_exactly_one_notification(
    q, session, repo_row, monkeypatch
) -> None:
    """A task run to done produces exactly one notification row."""
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})

    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_row.id, prompt="queue test done"
    )
    _install_adapter(
        monkeypatch,
        FakeHandle([AgentEvent(type="message", text="working"), AgentEvent(type="done")]),
    )

    q._run_task(task.id)

    session.expire_all()
    task_after = tasks.get_task(session, task.id)
    assert task_after.status == "done"

    notifs = session.execute(
        select(Notification).where(Notification.task_id == task.id)
    ).scalars().all()
    assert len(notifs) == 1
    notif = notifs[0]
    assert notif.kind == "task_done"
    assert notif.title == f"Task #{task.id} done"
    assert FULL_NAME in (notif.body or "")


def test_queue_run_failed_produces_exactly_one_notification(
    q, session, repo_row, monkeypatch
) -> None:
    """A task run to failed produces exactly one notification row."""
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})

    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_row.id, prompt="queue test failed"
    )
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="starting"),
                AgentEvent(type="error", text="fatal error"),
            ]
        ),
    )

    q._run_task(task.id)

    session.expire_all()
    task_after = tasks.get_task(session, task.id)
    assert task_after.status == "failed"

    notifs = session.execute(
        select(Notification).where(Notification.task_id == task.id)
    ).scalars().all()
    assert len(notifs) == 1
    notif = notifs[0]
    assert notif.kind == "task_failed"
    assert notif.title == f"Task #{task.id} failed"
    assert FULL_NAME in (notif.body or "")


def test_queue_run_waiting_input_produces_needs_input_notification(
    q, session, repo_row, monkeypatch
) -> None:
    """A task run with question produces a needs_input notification with truncated body."""
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})

    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_row.id, prompt="queue test waiting"
    )
    question_text = "Should I proceed with deleting the database table? " * 10
    _install_adapter(
        monkeypatch,
        FakeHandle([AgentEvent(type="message", text=question_text), AgentEvent(type="done")]),
    )

    q._run_task(task.id)

    session.expire_all()
    notifs = session.execute(
        select(Notification).where(Notification.task_id == task.id)
    ).scalars().all()
    assert len(notifs) == 1
    notif = notifs[0]
    assert notif.kind == "needs_input"
    assert notif.title == f"Task #{task.id} is waiting for input"
    assert notif.body is not None
    assert len(notif.body) <= 200
    assert notif.body == question_text[:200]


class _ReviewClient:
    """Minimal GitHubClient fake for review runs (records PR review posts)."""

    def __init__(self, head_sha: str):
        self.head_sha = head_sha
        self.posted: list[dict] = []
        self._id = 200

    def get_pr(self, full_name, number):
        return {"number": number, "head": "feature", "head_sha": self.head_sha}

    def set_commit_status(self, full_name, sha, state, context, description=None):
        self._id += 1
        return self._id

    def post_pr_review(self, full_name, pr_number, body):
        self.posted.append({"pr_number": pr_number, "body": body})

    def close(self):
        pass


def test_approval_waiting_review_notifies_needs_input_but_not_done(
    q, session, repo_row, monkeypatch, git_remote
) -> None:
    """A review ending in an approval question must not also announce completion.

    The run stays done with no review posted (approval gate), so exactly one
    needs_input notification is recorded and zero task_done rows.
    """
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})
    repo_row.check_runs_enabled = True
    session.commit()

    head = _git(["-C", git_remote, "rev-parse", "HEAD"])
    _git(["-C", git_remote, "update-ref", "refs/pull/7/head", head])
    client = _ReviewClient(head)
    monkeypatch.setattr("jalebi.queue.GitHubClient", lambda token: client)
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="Plan ready. Shall I post this review?"),
                AgentEvent(type="done"),
            ]
        ),
    )

    task = tasks.create_task(
        session,
        type_="pr_review",
        repo_id=repo_row.id,
        prompt="Review the PR.",
        target_branch="main",
        pat_name="test",
        prs=[7],
    )
    q._run_task(task.id)

    session.expire_all()
    assert client.posted == []
    notifs = session.execute(
        select(Notification).where(Notification.task_id == task.id)
    ).scalars().all()
    assert [n.kind for n in notifs] == ["needs_input"]

