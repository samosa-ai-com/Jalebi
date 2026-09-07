"""Screening API tests (PRD F10)."""

import json
import threading
import time

import pytest

from jalebi import repos, secrets

FULL_NAME = "owner/screenapi"


@pytest.fixture
def repo_row(session):
    row, _ = repos.upsert_repo(
        session, full_name=FULL_NAME, default_branch="main",
        clone_url="https://example.com/owner/screenapi.git", pat_name="test",
    )
    return row


@pytest.fixture(autouse=True)
def _token(app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")
    yield
    secrets.remove_github_token(app.config["JALEBI_CONFIG"], "test")


def _payload(repo_row, **overrides):
    body = dict(
        repo_id=repo_row.id,
        name="Security posture",
        system_prompt="Audit security.",
        cadence_cron="0 6 * * 1",
        notify_ntfy=True,
    )
    body.update(overrides)
    return body


def test_templates_endpoint(client):
    res = client.get("/api/screenings/templates")
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "system_prompt" in data[0]


def test_create_and_list_screen(client, repo_row):
    res = client.post("/api/screenings", json=_payload(repo_row))
    assert res.status_code == 201
    screen = res.get_json()
    assert screen["name"] == "Security posture"

    res = client.get("/api/screenings")
    assert res.status_code == 200
    assert any(s["id"] == screen["id"] for s in res.get_json())
    assert all("latest_run" in s for s in res.get_json())


def test_create_validates(client, repo_row):
    res = client.post("/api/screenings", json={})
    assert res.status_code == 400
    res = client.post("/api/screenings", json=_payload(repo_row, cadence_cron="bogus"))
    assert res.status_code == 400
    assert "cadence_cron" in res.get_json()["error"]


def test_create_update_cli_model_pins(client, repo_row):
    """Screens accept backend (cli) + model pins; an unknown cli is rejected."""
    res = client.post(
        "/api/screenings", json=_payload(repo_row, cli="opencode", model="m-9")
    )
    assert res.status_code == 201
    body = res.get_json()
    assert body["cli"] == "opencode"
    assert body["model"] == "m-9"

    res = client.post("/api/screenings", json=_payload(repo_row, cli="codex"))
    assert res.status_code == 201
    assert res.get_json()["cli"] == "codex"

    res = client.post("/api/screenings", json=_payload(repo_row, cli="gemini"))
    assert res.status_code == 400
    assert "unsupported agent cli" in res.get_json()["error"]

    # Update pins, then clear them with an empty string.
    sid = body["id"]
    res = client.put(f"/api/screenings/{sid}", json={"model": "m-10"})
    assert res.status_code == 200
    assert res.get_json()["model"] == "m-10"
    res = client.put(f"/api/screenings/{sid}", json={"cli": "", "model": ""})
    assert res.status_code == 200
    cleared = res.get_json()
    assert cleared["cli"] is None
    assert cleared["model"] is None


def test_get_update_delete(client, repo_row):
    created = client.post("/api/screenings", json=_payload(repo_row)).get_json()
    sid = created["id"]

    res = client.get(f"/api/screenings/{sid}")
    assert res.status_code == 200
    assert res.get_json()["id"] == sid

    res = client.put(f"/api/screenings/{sid}", json={"enabled": False, "notify_ntfy": False})
    assert res.status_code == 200
    body = res.get_json()
    assert body["enabled"] is False
    assert body["notify_ntfy"] is False

    res = client.put(f"/api/screenings/{sid}", json={"cadence_cron": "bogus"})
    assert res.status_code == 400

    res = client.delete(f"/api/screenings/{sid}")
    assert res.status_code == 200
    assert client.get(f"/api/screenings/{sid}").status_code == 404
    assert client.delete(f"/api/screenings/{sid}").status_code == 404


def test_runs_endpoint_empty_then_run(client, repo_row, monkeypatch):
    created = client.post("/api/screenings", json=_payload(repo_row)).get_json()
    sid = created["id"]

    res = client.get(f"/api/screenings/{sid}/runs")
    assert res.status_code == 200
    assert res.get_json() == []

    # Run now is async: stub the engine's run_screen so nothing executes.
    monkeypatch.setattr(
        "jalebi.screening.ScreeningEngine.run_screen",
        lambda self, session, screen, *, force=False: None,
    )
    res = client.post(f"/api/screenings/{sid}/run")
    assert res.status_code == 200
    assert res.get_json()["ok"] is True
    # Give the daemon thread a beat to finish its (stubbed) work.
    time.sleep(0.05)
    assert threading.active_count() >= 1


def test_unknown_screen_404(client):
    assert client.get("/api/screenings/999").status_code == 404
    assert client.get("/api/screenings/999/runs").status_code == 404
    assert client.put("/api/screenings/999", json={}).status_code == 404
    assert client.delete("/api/screenings/999").status_code == 404
    assert client.post("/api/screenings/999/run").status_code == 404


def test_create_rejects_strict_types(client, repo_row):
    """Booleans must be real booleans (`bool('false')` is True); repo_id must
    be a real int (`True` is an int subclass); pins must be strings."""
    res = client.post("/api/screenings", json=_payload(repo_row, enabled="false"))
    assert res.status_code == 400
    res = client.post("/api/screenings", json=_payload(repo_row, repo_id=True))
    assert res.status_code == 400
    res = client.post("/api/screenings", json=_payload(repo_row, cli=123))
    assert res.status_code == 400
    res = client.post("/api/screenings", json=_payload(repo_row, notify_ntfy=1))
    assert res.status_code == 400


def test_update_null_clears_and_omitted_keeps(client, repo_row):
    """PUT is present-key: explicit null clears nullable fields, omitted keys
    (including booleans) are left alone."""
    res = client.post(
        "/api/screenings",
        json=_payload(repo_row, scope_branch="main", cli="opencode", model="m-1"),
    )
    assert res.status_code == 201
    screen_id = res.get_json()["id"]

    res = client.put(f"/api/screenings/{screen_id}", json={"name": "Renamed"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["name"] == "Renamed"
    assert body["scope_branch"] == "main"  # omitted → kept
    assert body["enabled"] is True  # omitted bool → kept (was reset to False before)

    res = client.put(
        f"/api/screenings/{screen_id}",
        json={"scope_branch": None, "cli": None, "model": None},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["scope_branch"] is None
    assert body["cli"] is None
    assert body["model"] is None


def test_preflight_failure_records_failed_run_masked(client, repo_row, app, monkeypatch):
    """A 'Run now' whose preflight fails must leave a failed, secret-masked run
    in history instead of vanishing into the server log."""
    import time as _time

    from jalebi import screening as screening_mod

    res = client.post("/api/screenings", json=_payload(repo_row))
    assert res.status_code == 201
    screen_id = res.get_json()["id"]

    def _boom(session, screen, force=False):
        raise screening_mod.ScreeningError("no PAT account bound (token ghp_test)")

    monkeypatch.setattr(
        app.config["JALEBI_SCREENING"].engine, "run_screen", _boom
    )
    res = client.post(f"/api/screenings/{screen_id}/run")
    assert res.status_code == 200

    deadline = _time.time() + 10
    runs = []
    while _time.time() < deadline:
        runs = client.get(f"/api/screenings/{screen_id}/runs").get_json()
        if runs and runs[0]["status"] == "failed":
            break
        _time.sleep(0.2)
    assert runs and runs[0]["status"] == "failed"
    assert "ghp_test" not in (runs[0]["error"] or "")
    assert "***" in (runs[0]["error"] or "")


def test_unknown_run_events_404(client):
    """Subscribing to a nonexistent run answers 404 immediately (no hang)."""
    res = client.get("/api/screenings/runs/999999/events")
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_list_latest_run_is_summary(client, repo_row, session):
    """The list endpoint ships a lightweight summary per screen — counts, no
    findings blob, no 50KB output."""
    from jalebi.db import ScreeningRun

    res = client.post("/api/screenings", json=_payload(repo_row))
    screen_id = res.get_json()["id"]
    session.add(
        ScreeningRun(
            screening_id=screen_id,
            head_sha="abc",
            status="done",
            findings_json='[{"severity": "high", "title": "T"},'
            '{"severity": "high", "title": "U"},'
            '{"severity": "low", "title": "V"}]',
            output_json='{"message": "' + ("x" * 40000) + '"}',
        )
    )
    session.commit()

    items = client.get("/api/screenings").get_json()
    mine = next(s for s in items if s["id"] == screen_id)
    latest = mine["latest_run"]
    assert latest["status"] == "done"
    assert latest["finding_total"] == 3
    assert latest["finding_counts"] == {"high": 2, "low": 1}
    assert "findings" not in latest
    assert "output" not in latest


def test_run_now_409_when_already_running(client, repo_row, app):
    """A synchronous 409 when the per-screen lock is held — the conflict is
    inline, not a silent no-op discovered after the 200."""
    import threading as _threading

    res = client.post("/api/screenings", json=_payload(repo_row))
    screen_id = res.get_json()["id"]
    engine = app.config["JALEBI_SCREENING"].engine
    lock = engine._locks.setdefault(screen_id, _threading.Lock())
    lock.acquire()
    try:
        res = client.post(f"/api/screenings/{screen_id}/run")
        assert res.status_code == 409
        assert "already running" in res.get_json()["error"]
    finally:
        lock.release()


def test_runtime_failure_records_single_row_no_ghost(client, repo_row, app, monkeypatch, session):
    """A runtime failure (row already committed by the engine) must not gain a
    second ghost row from the route's preflight recorder."""
    import time as _time

    from jalebi import screening as screening_mod
    from jalebi.db import ScreeningRun

    res = client.post("/api/screenings", json=_payload(repo_row))
    screen_id = res.get_json()["id"]

    def _fail_after_row(session, screen, force=False):
        run = ScreeningRun(screening_id=screen.id, head_sha="abc", status="running")
        session.add(run)
        session.commit()
        raise screening_mod.ScreeningError("agent exploded")

    monkeypatch.setattr(
        app.config["JALEBI_SCREENING"].engine, "run_screen", _fail_after_row
    )
    assert client.post(f"/api/screenings/{screen_id}/run").status_code == 200

    deadline = _time.time() + 10
    while _time.time() < deadline:
        count = session.query(ScreeningRun).filter_by(screening_id=screen_id).count()
        if count >= 1:
            break
        _time.sleep(0.2)
    _time.sleep(0.5)  # let a wrongful ghost row appear if the bug is present
    session.expire_all()
    rows = session.query(ScreeningRun).filter_by(screening_id=screen_id).all()
    # Exactly the engine's own row — the route must not add a ghost preflight row.
    assert len(rows) == 1
    assert rows[0].head_sha == "abc"


def _make_run(session, screen_id, findings, head="abc"):
    from jalebi.db import ScreeningRun

    row = ScreeningRun(
        screening_id=screen_id, head_sha=head, status="done",
        findings_json=json.dumps(findings),
    )
    session.add(row)
    session.commit()
    return row


def test_findings_inbox_newest_first_with_context(client, repo_row, session):
    """The inbox flattens runs newest-first with screen/repo/run context."""
    r1 = client.post("/api/screenings", json=_payload(repo_row, name="Older"))
    r2 = client.post("/api/screenings", json=_payload(repo_row, name="Newer"))
    id1, id2 = r1.get_json()["id"], r2.get_json()["id"]
    _make_run(session, id1, [{"severity": "low", "title": "Old finding"}], head="aaa")
    _make_run(
        session, id2,
        [
            {"severity": "high", "title": "New finding", "file": "a.py", "line": 1},
            {"severity": "medium", "title": "Second"},
        ],
        head="bbb",
    )
    res = client.get("/api/screenings/findings")
    assert res.status_code == 200
    items = res.get_json()
    assert [i["title"] for i in items] == ["New finding", "Second", "Old finding"]
    first = items[0]
    assert first["screen_name"] == "Newer"
    assert first["repo_full_name"] == FULL_NAME
    assert first["run_id"] and first["head_sha"] == "bbb"
    assert first["file"] == "a.py" and first["line"] == 1


def test_findings_inbox_filters(client, repo_row, session):
    r1 = client.post("/api/screenings", json=_payload(repo_row, name="S1"))
    r2 = client.post("/api/screenings", json=_payload(repo_row, name="S2"))
    id1, id2 = r1.get_json()["id"], r2.get_json()["id"]
    _make_run(session, id1, [{"severity": "high", "title": "H"}])
    _make_run(session, id2, [{"severity": "low", "title": "L"}])

    res = client.get("/api/screenings/findings?severity=high")
    assert [i["title"] for i in res.get_json()] == ["H"]
    res = client.get(f"/api/screenings/findings?screen_id={id2}")
    assert [i["title"] for i in res.get_json()] == ["L"]
    assert client.get("/api/screenings/findings?severity=bogus").status_code == 400
    assert client.get("/api/screenings/findings?limit=nope").status_code == 400
    assert client.get("/api/screenings/findings?screen_id=999999").status_code == 404
    limited = client.get("/api/screenings/findings?limit=1").get_json()
    assert len(limited) == 1


def test_findings_inbox_empty(client, repo_row):
    client.post("/api/screenings", json=_payload(repo_row))
    res = client.get("/api/screenings/findings")
    assert res.status_code == 200
    assert res.get_json() == []


def test_findings_inbox_rejects_bad_screen_id(client, repo_row):
    client.post("/api/screenings", json=_payload(repo_row))
    assert client.get("/api/screenings/findings?screen_id=nope").status_code == 400


def test_findings_inbox_coerces_malformed_rows(client, repo_row, session):
    """Hand-inserted/garbage finding shapes degrade gracefully — the UI render
    must never crash on a non-string title/severity/line."""
    from jalebi.db import ScreeningRun

    res = client.post("/api/screenings", json=_payload(repo_row))
    session.add(
        ScreeningRun(
            screening_id=res.get_json()["id"],
            head_sha="abc",
            status="done",
            findings_json=json.dumps(
                [
                    {"severity": "bogus", "title": 42, "file": ["x"], "line": True},
                    "not-a-dict",
                ]
            ),
        )
    )
    session.commit()
    items = client.get("/api/screenings/findings").get_json()
    assert len(items) == 1
    assert items[0]["severity"] == "medium"
    assert isinstance(items[0]["title"], str)
    assert items[0]["file"] is None
    assert items[0]["line"] is None
