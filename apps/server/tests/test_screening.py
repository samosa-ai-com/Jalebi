"""Screening engine tests (PRD F10): run loop, dedup, parse, notify, scheduler."""

import json
import subprocess

import pytest

from jalebi import repos, screening, secrets, settings
from jalebi.adapters.types import AgentEvent
from jalebi.screening import ScreeningEngine, ScreeningScheduler

FULL_NAME = "owner/screenrepo"


def _git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class FakeProc:
    def poll(self):
        return None

    def wait(self, timeout=None) -> int:
        return 0


class FakeHandle:
    def __init__(self, events):
        self._events = list(events)
        self.session_id = "ses_screen"
        self.proc = FakeProc()

    def events(self):
        yield from self._events


def _install_adapter(monkeypatch, handle) -> None:
    class FakeAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            return handle

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.screening.get_adapter", lambda cli: FakeAdapter())


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
def repo_row(session, git_remote):
    row, _ = repos.upsert_repo(
        session, full_name=FULL_NAME, default_branch="main", clone_url=git_remote, pat_name="test"
    )
    return row


@pytest.fixture(autouse=True)
def _token(app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")
    yield
    secrets.remove_github_token(app.config["JALEBI_CONFIG"], "test")


@pytest.fixture
def engine(app):
    return ScreeningEngine(app.config["JALEBI_CONFIG"])


def _make_screen(session, repo_row, **overrides):
    """Create a screen with type-safe defaults; ``overrides`` are keyword params."""
    return screening.create_screen(
        session,
        repo_id=repo_row.id,
        name=str(overrides.get("name", "Security posture")),
        system_prompt=str(overrides.get("system_prompt", "Audit security.")),
        cadence_cron=str(overrides.get("cadence_cron", "0 6 * * 1")),
        enabled=bool(overrides.get("enabled", True)),
        notify_ntfy=bool(overrides.get("notify_ntfy", True)),
    )


def _done_events(findings_json: str) -> list[AgentEvent]:
    return [
        AgentEvent(type="step", phase="scanning"),
        AgentEvent(type="message", text=findings_json),
        AgentEvent(type="done"),
    ]


def test_parse_findings_valid_json():
    raw = (
        '[{"severity":"high","title":"Secret in config","file":"a.py","line":3,'
        '"detail":"x","recommendation":"y"}]'
    )
    out = screening.parse_findings(raw)
    assert len(out) == 1
    assert out[0]["severity"] == "high"
    assert out[0]["title"] == "Secret in config"


def test_parse_findings_fenced_and_prose():
    raw = '```json\n[{"severity":"low","title":"T"}\n]\n```'
    out = screening.parse_findings(raw)
    assert len(out) == 1
    assert out[0]["title"] == "T"


def test_parse_findings_bad_inputs():
    assert screening.parse_findings("") == []
    assert screening.parse_findings("no json here") == []
    assert screening.parse_findings("not even an array") == []
    assert screening.parse_findings('{"a":1}') == []
    assert screening.parse_findings('[{], broken') == []


def test_parse_findings_normalizes_severity_and_drops_junk():
    raw = '[{"severity":"blah","title":"A"},{"title":"B"},{"title":5},{"detail":"d"}]'
    out = screening.parse_findings(raw)
    assert len(out) == 2
    assert out[0]["severity"] == "medium"
    assert out[1]["title"] == "B"


def test_parse_findings_ignores_brackets_inside_strings():
    raw = '[{"severity":"low","title":"T","detail":"line ] 2 and [x"}]'
    out = screening.parse_findings(raw)
    assert len(out) == 1
    assert out[0]["detail"] == "line ] 2 and [x"


def test_run_screen_findings_are_masked(session, repo_row, engine, monkeypatch):
    payload = '[{"severity":"high","title":"Token ghp_ABCDE leaked","file":"a.py"}]'
    _install_adapter(monkeypatch, FakeHandle(_done_events(payload)))
    # Add the fake token to the vault so the masker redacts it.
    secrets.add_github_token(engine.config, "maskme", "ghp_ABCDE")
    screen = _make_screen(session, repo_row)
    run = engine.run_screen(session, screen)
    findings = json.loads(run.findings_json or "[]")
    assert len(findings) == 1
    assert "ghp_ABCDE" not in findings[0]["title"]
    assert "***" in findings[0]["title"]
    # The raw output stored on the run is also masked.
    assert "ghp_ABCDE" not in (run.output_json or "")


def test_run_screen_produces_findings(session, repo_row, engine, monkeypatch):
    _install_adapter(monkeypatch, FakeHandle(_done_events('[]')))
    screen = _make_screen(session, repo_row)
    run = engine.run_screen(session, screen)
    assert run is not None
    assert run.status == "done"
    assert run.head_sha is not None
    assert json.loads(run.findings_json or "[]") == []


def test_run_screen_stores_parsed_findings(session, repo_row, engine, monkeypatch):
    payload = '[{"severity":"high","title":"CVE","file":"dep.txt","line":1}]'
    _install_adapter(monkeypatch, FakeHandle(_done_events(payload)))
    screen = _make_screen(session, repo_row)
    run = engine.run_screen(session, screen)
    assert run.status == "done"
    findings = json.loads(run.findings_json or "[]")
    assert len(findings) == 1
    assert findings[0]["title"] == "CVE"


def test_run_screen_marks_failed_on_agent_error(session, repo_row, engine, monkeypatch):
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="boom"),
                AgentEvent(type="error", text="crash"),
            ]
        ),
    )
    screen = _make_screen(session, repo_row)
    run = engine.run_screen(session, screen)
    assert run.status == "failed"
    assert run.error == "agent exited with an error"


def test_run_screen_baseline_dedup(session, repo_row, engine, monkeypatch):
    _install_adapter(monkeypatch, FakeHandle(_done_events('[]')))
    screen = _make_screen(session, repo_row)
    first = engine.run_screen(session, screen)
    assert first is not None
    # Same HEAD, not forced → baseline dedup skips.
    second = engine.run_screen(session, screen)
    assert second is None
    assert len(screening.list_runs(session, screen.id)) == 1
    # Force ignores dedup.
    third = engine.run_screen(session, screen, force=True)
    assert third is not None
    assert len(screening.list_runs(session, screen.id)) == 2


def test_run_screen_no_account_fails(session, repo_row, engine, monkeypatch):
    secrets.remove_github_token(engine.config, "test")
    screen = _make_screen(session, repo_row)
    with pytest.raises(screening.ScreeningError, match="no PAT"):
        engine.run_screen(session, screen)


def test_notify_skipped_when_disabled(session, repo_row, engine, monkeypatch, tmp_path):
    sent = {}

    def fake_send(session, title, message, **kwargs):
        sent["title"] = title

    monkeypatch.setattr("jalebi.screening.notify.send", fake_send)
    _install_adapter(monkeypatch, FakeHandle(_done_events('[{"severity":"high","title":"X"}]')))
    screen = _make_screen(session, repo_row, notify_ntfy=False)
    engine.run_screen(session, screen)
    assert sent == {}


def test_notify_sends_when_enabled(session, repo_row, engine, monkeypatch):
    sent = {}

    def fake_send(session, title, message, **kwargs):
        sent["title"] = title
        sent["message"] = message

    monkeypatch.setattr("jalebi.screening.notify.send", fake_send)
    _install_adapter(monkeypatch, FakeHandle(_done_events('[{"severity":"high","title":"X"}]')))
    screen = _make_screen(session, repo_row, notify_ntfy=True)
    engine.run_screen(session, screen)
    assert "1 finding" in sent.get("title", "")
    assert "X" in sent.get("message", "")


def test_create_screen_validates_cron(session, repo_row):
    with pytest.raises(screening.ScreeningError, match="cadence_cron"):
        screening.create_screen(
            session, repo_id=repo_row.id, name="N", system_prompt="P", cadence_cron="bad cron"
        )
    with pytest.raises(screening.ScreeningError, match="name"):
        _make_screen(session, repo_row, name="")


def test_update_and_delete(session, repo_row):
    screen = _make_screen(session, repo_row)
    screening.update_screen(session, screen, enabled=False, notify_ntfy=False)
    assert not screen.enabled and not screen.notify_ntfy
    assert screening.delete_screen(session, screen.id)
    assert not screening.delete_screen(session, screen.id)
    assert screening.get_screen(session, screen.id) is None


def test_scheduler_due_screens_and_tick(app, session, repo_row, monkeypatch):
    from datetime import datetime

    settings.set_setting(session, "ntfy_topic", "topic")
    screen = _make_screen(session, repo_row, cadence_cron="* * * * *")
    disabled = _make_screen(session, repo_row, name="off", cadence_cron="* * * * *", enabled=False)
    scheduler = ScreeningScheduler(app.config["JALEBI_CONFIG"])
    now = datetime(2026, 8, 9, 6, 0)
    due = scheduler._due_screens(session, now)
    ids = {s.id for s in due}
    assert screen.id in ids
    assert disabled.id not in ids

    # tick runs the due screen (fake adapter) and records a run
    _install_adapter(monkeypatch, FakeHandle(_done_events('[]')))
    ran = scheduler.tick(now=now)
    assert ran == 1
    runs = screening.list_runs(session, screen.id)
    assert len(runs) == 1
    assert runs[0].status == "done"


def test_scheduler_skips_when_none_due(app, session, repo_row, monkeypatch):
    scheduler = ScreeningScheduler(app.config["JALEBI_CONFIG"])
    _make_screen(session, repo_row, cadence_cron="0 6 * * 1")
    from datetime import datetime

    _install_adapter(monkeypatch, FakeHandle(_done_events('[]')))
    # Tuesday 08:00 → the Monday 06:00 screen is not due.
    ran = scheduler.tick(now=datetime(2026, 8, 11, 8, 0))
    assert ran == 0
