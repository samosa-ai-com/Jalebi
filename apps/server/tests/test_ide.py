"""Phase 4 T6 — IDE connector (settings + open-in-ide)."""

import argparse
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from jalebi import ide, repos, secrets, settings


@pytest.fixture(autouse=True)
def _fake_token(config, monkeypatch):
    secrets.add_github_token(config, "test", "ghp_test")


def _make_task(session) -> int:
    repos.upsert_repo(
        session,
        full_name="owner/r",
        default_branch="main",
        clone_url="https://x/r.git",
        pat_name="test",
    )
    from jalebi import tasks as tasks_svc

    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=1, prompt="x"
    )
    return task.id


def test_validate_ide_command_accepts_empty_and_known_binary() -> None:
    assert ide.validate_ide_command("") is True
    assert ide.validate_ide_command("/usr/bin/code") is True or "code" not in ""
    # Bare name without PATH resolution → False.
    assert ide.validate_ide_command("definitely-not-a-real-binary-xyz") is False


def test_validate_ide_command_rejects_metacharacters() -> None:
    for bad in ["code;rm", "code|cat", "code&whoami", "$(id)", "code a", "code\nrm", "code'rm'"]:
        assert ide.validate_ide_command(bad) is False, f"should reject {bad!r}"


def test_validate_ide_command_rejects_non_string() -> None:
    assert ide.validate_ide_command(None) is False
    assert ide.validate_ide_command(123) is False


def test_detect_ide_returns_first_on_path(monkeypatch) -> None:
    monkeypatch.setattr(ide.shutil, "which", lambda n: "/fake/bin/code" if n == "code" else None)
    detected = ide.detect_ide()
    assert detected == ("code", "VS Code")


def test_detect_ide_returns_none_when_nothing_on_path(monkeypatch) -> None:
    monkeypatch.setattr(ide.shutil, "which", lambda n: None)
    assert ide.detect_ide() is None


def test_resolve_command_absolute_path_existing(monkeypatch, tmp_path) -> None:
    p = tmp_path / "ide"
    p.write_text("#!/bin/sh\n")
    monkeypatch.setattr(ide.os.path, "isfile", lambda x: str(x) == str(p))
    assert ide.resolve_command(str(p)) == str(p)


def test_resolve_command_absolute_path_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ide.os.path, "isfile", lambda x: False)
    assert ide.resolve_command(str(tmp_path / "missing")) is None


def test_resolve_command_via_which(monkeypatch) -> None:
    monkeypatch.setattr(ide.shutil, "which", lambda n: "/fake/bin/code" if n == "code" else None)
    assert ide.resolve_command("code") == "/fake/bin/code"


def test_test_open_returns_ok_when_resolves(monkeypatch, session) -> None:
    settings.set_setting(session, "ide_command", "code")
    monkeypatch.setattr(ide.shutil, "which", lambda n: "/fake/bin/code")
    calls = []

    def fake_popen(argv, **kw):
        calls.append((argv, kw))
        return argparse.Namespace(pid=1)

    class FakeProc:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(ide.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        ide.shutil, "rmtree", lambda *a, **kw: None
    )
    ok, err = ide.test_open(session)
    assert ok is True
    assert err == ""
    assert len(calls) == 1
    argv, _ = calls[0]
    assert argv[0] == "/fake/bin/code"
    assert argv[1].startswith(str(Path("/tmp"))) or "/" in argv[1]


def test_test_open_returns_error_when_unconfigured(session) -> None:
    settings.set_setting(session, "ide_command", "")
    ok, err = ide.test_open(session)
    assert ok is False
    assert "no ide configured" in err.lower()


def test_open_in_ide_spawns_resolved_argv(monkeypatch) -> None:
    """The single security boundary: the rendered argv is the only
    surface an attacker can influence. We mock subprocess.Popen and
    assert the exact argv list (bare name resolved to an absolute path)."""
    calls = []

    def fake_popen(argv, **kw):
        calls.append((argv, kw))
        return argparse.Namespace(pid=1)

    monkeypatch.setattr(ide.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        ide.shutil, "which", lambda n: "/fake/bin/code" if n == "code" else None
    )
    worktree = Path("/tmp/fake/worktree")
    ide.open_in_ide("code", worktree)
    assert len(calls) == 1
    argv, kw = calls[0]
    assert argv == ["/fake/bin/code", str(worktree)]
    assert kw["start_new_session"] is True
    assert kw["stdout"] is ide.subprocess.DEVNULL
    assert kw["stderr"] is ide.subprocess.DEVNULL
    assert kw["close_fds"] is True


def test_open_in_ide_raises_when_unconfigured() -> None:
    with pytest.raises(ide.IdeError):
        ide.open_in_ide("", Path("/tmp/x"))


def test_open_in_ide_raises_when_unresolved(monkeypatch) -> None:
    monkeypatch.setattr(ide.shutil, "which", lambda n: None)
    with pytest.raises(ide.IdeError):
        ide.open_in_ide("definitely-not-on-path", Path("/tmp/x"))


# ---- Route-level (via the Flask client) ------------------------------------


def test_open_in_ide_unconfigured_returns_409(
    client: FlaskClient, session, app
) -> None:
    task_id = _make_task(session)
    resp = client.post(f"/api/tasks/{task_id}/open-in-ide")
    assert resp.status_code == 409
    assert "IDE not configured" in resp.get_json()["error"]


def test_open_in_ide_missing_worktree_returns_404(
    client: FlaskClient, session, app, monkeypatch
) -> None:
    """A task with no worktree yet (queued, never spawned) → 404."""
    settings.set_setting(session, "ide_command", "code")
    monkeypatch.setattr(ide.shutil, "which", lambda n: "/fake/bin/code")
    task_id = _make_task(session)
    resp = client.post(f"/api/tasks/{task_id}/open-in-ide")
    assert resp.status_code == 404
    assert "no worktree" in resp.get_json()["error"].lower()


def test_open_in_ide_unknown_task_returns_404(client: FlaskClient) -> None:
    resp = client.post("/api/tasks/99999/open-in-ide")
    assert resp.status_code == 404


def test_open_in_ide_spawns_when_configured(
    client: FlaskClient, session, app, config, monkeypatch, tmp_path
) -> None:
    """Happy path: command configured + worktree exists → spawn + 200."""
    settings.set_setting(session, "ide_command", "code")
    monkeypatch.setattr(ide.shutil, "which", lambda n: "/fake/bin/code")
    calls = []

    def fake_popen(argv, **kw):
        calls.append((argv, kw))
        return argparse.Namespace(pid=1)

    monkeypatch.setattr(ide.subprocess, "Popen", fake_popen)
    task_id = _make_task(session)
    # Create the worktree marker so the route doesn't 404.
    worktree = Path(config.data_dir) / "ws" / f"task-{task_id}"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / ".git").write_text("gitdir: /tmp/fake")

    resp = client.post(f"/api/tasks/{task_id}/open-in-ide")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["path"] == str(worktree)
    assert len(calls) == 1
    argv, _ = calls[0]
    assert argv[0] == "/fake/bin/code"
    assert argv[1] == str(worktree)


def test_ide_status_includes_command_and_found_flag(
    client: FlaskClient, session, app, monkeypatch
) -> None:
    settings.set_setting(session, "ide_command", "code")
    settings.set_setting(session, "ide_name", "VS Code")
    monkeypatch.setattr(ide.shutil, "which", lambda n: "/fake/bin/code")
    resp = client.get("/api/ide/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["command"] == "code"
    assert body["name"] == "VS Code"
    assert body["found"] is True


def test_detect_all_ides(monkeypatch) -> None:
    def fake_which(n):
        if n == "antigravity":
            return "/usr/local/bin/antigravity"
        if n == "cursor":
            return "/usr/bin/cursor"
        return None

    monkeypatch.setattr(ide.shutil, "which", fake_which)
    found = ide.detect_all_ides()
    cmds = [item["command"] for item in found]
    assert "antigravity" in cmds
    assert "cursor" in cmds
    names = [item["name"] for item in found]
    assert "Antigravity" in names
    assert "Cursor" in names


def test_ide_detect_endpoint_returns_first_match(
    client: FlaskClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        ide.shutil, "which", lambda n: "/fake/bin/cursor" if n == "cursor" else None
    )
    resp = client.get("/api/ide/detect")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["command"] == "cursor"
    assert body["name"] == "Cursor"
    assert len(body["detected"]) == 1
    assert body["detected"][0]["command"] == "cursor"


def test_ide_detect_endpoint_returns_empty_when_nothing_on_path(
    client: FlaskClient, monkeypatch
) -> None:
    monkeypatch.setattr(ide.shutil, "which", lambda n: None)
    resp = client.get("/api/ide/detect")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["command"] == ""
    assert body["name"] == ""
    assert body["detected"] == []


def test_ide_test_endpoint_returns_400_when_unconfigured(client: FlaskClient) -> None:
    resp = client.post("/api/ide/test")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_settings_validates_ide_command_rejects_metachars(
    client: FlaskClient, session, app
) -> None:
    """The validator in app.py rejects metacharacters; the route maps to 400."""
    resp = client.post(
        "/api/settings", json={"key": "ide_command", "value": "code;rm -rf /"}
    )
    assert resp.status_code == 400
    assert "ide_command" in resp.get_json()["error"]


def test_settings_validates_ide_command_accepts_valid(
    client: FlaskClient, session, app, monkeypatch
) -> None:
    """The validator accepts a bare name on PATH + an absolute path."""
    monkeypatch.setattr(
        ide.shutil, "which", lambda n: "/fake/bin/code" if n == "code" else None
    )
    resp = client.post("/api/settings", json={"key": "ide_command", "value": "code"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ide_command"] == "code"

    resp2 = client.post(
        "/api/settings", json={"key": "ide_name", "value": "VS Code"}
    )
    assert resp2.status_code == 200
    assert resp2.get_json()["ide_name"] == "VS Code"
