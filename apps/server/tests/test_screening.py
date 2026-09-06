"""Screening engine tests (PRD F10): run loop, dedup, parse, notify, scheduler."""

import json
import subprocess
import threading
import time
from datetime import timedelta

import pytest

from jalebi import repos, screening, secrets, settings
from jalebi.adapters.types import AgentEvent
from jalebi.db import ScreeningRun, now
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


def test_run_screen_uses_screen_cli_and_model(session, repo_row, engine, monkeypatch):
    """The engine resolves the screen's backend + model pins on the adapter."""
    captured: dict[str, object] = {}

    class FakeAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["model"] = model
            return FakeHandle(_done_events("[]"))

        def list_models(self):
            return []

    def fake_get_adapter(cli):
        captured["cli"] = cli
        return FakeAdapter()

    monkeypatch.setattr("jalebi.screening.get_adapter", fake_get_adapter)
    screen = screening.create_screen(
        session,
        repo_id=repo_row.id,
        name="S",
        system_prompt="P",
        cadence_cron="0 6 * * 1",
        cli="opencode",
        model="m-9",
    )
    engine.run_screen(session, screen, force=True)
    assert captured["cli"] == "opencode"
    assert captured["model"] == "m-9"


def test_unpinned_screen_uses_global_default_backend(session, repo_row, engine, monkeypatch):
    """A screen with no backend pin resolves from the default_backend setting
    (parity with the task queue) — the global default, not hardcoded opencode."""
    from jalebi import settings

    settings.set_setting(session, "default_backend", "codex")
    # The codex sandbox gate would refuse a codex screening on a host without a
    # usable bwrap workspace-write sandbox; mock the probe so the test exercises
    # the resolution path independently of the host.
    monkeypatch.setattr(screening, "_codex_sandbox_usable", lambda: True)
    captured: list[tuple] = []
    monkeypatch.setattr(
        "jalebi.screening.worktree_bootstrap.write_guard",
        lambda wt, cli: captured.append((wt, cli)),
    )
    adapter_seen: list[str] = []

    class FakeAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            return FakeHandle(_done_events("[]"))

        def list_models(self):
            return []

    def fake_get_adapter(cli):
        adapter_seen.append(cli)
        return FakeAdapter()

    monkeypatch.setattr("jalebi.screening.get_adapter", fake_get_adapter)
    screen = screening.create_screen(
        session,
        repo_id=repo_row.id,
        name="Unpinned",
        system_prompt="P",
        cadence_cron="0 6 * * 1",
        cli=None,
    )
    engine.run_screen(session, screen, force=True)
    assert adapter_seen == ["codex"]
    assert captured and captured[-1][1] == "codex"
    # A screen with its own pin still wins over the setting.
    settings.set_setting(session, "default_backend", "claude")
    pinned = screening.create_screen(
        session,
        repo_id=repo_row.id,
        name="Pinned",
        system_prompt="P",
        cadence_cron="0 6 * * 2",
        cli="opencode",
    )
    engine.run_screen(session, pinned, force=True)
    assert adapter_seen[-1] == "opencode"
    assert captured[-1][1] == "opencode"


def test_run_screen_writes_per_cli_guard(session, repo_row, engine, monkeypatch):
    """The audit worktree gets the guard matching the screen's backend."""
    # Mock the codex sandbox probe so the test exercises the per-cli guard write
    # independently of whether the host's bwrap is usable.
    monkeypatch.setattr(screening, "_codex_sandbox_usable", lambda: True)
    captured: list[tuple] = []
    monkeypatch.setattr(
        "jalebi.screening.worktree_bootstrap.write_guard",
        lambda wt, cli: captured.append((wt, cli)),
    )
    _install_adapter(monkeypatch, FakeHandle(_done_events("[]")))

    codex_screen = screening.create_screen(
        session,
        repo_id=repo_row.id,
        name="Codex Audit",
        system_prompt="P",
        cadence_cron="0 6 * * 1",
        cli="codex",
    )
    engine.run_screen(session, codex_screen, force=True)
    assert captured and captured[-1][1] == "codex"

    default_screen = screening.create_screen(
        session,
        repo_id=repo_row.id,
        name="Default Audit",
        system_prompt="P",
        cadence_cron="0 6 * * 2",
        cli=None,
    )
    engine.run_screen(session, default_screen, force=True)
    assert captured and captured[-1][1] == "opencode"


def test_codex_screening_refused_when_sandbox_unusable(
    session, repo_row, engine, monkeypatch
) -> None:
    """Screening audits the most prompt-injection-exposed code in the system —
    a codex screening is refused on a host without a usable workspace-write
    sandbox (the only disk confinement codex has). opencode/claude keep their
    pattern-gate floor even without an OS sandbox.
    """
    monkeypatch.setattr(screening, "_codex_sandbox_usable", lambda: False)
    # No adapter call should ever happen — refuse before resolve.
    called: list[str] = []
    monkeypatch.setattr(
        "jalebi.screening.get_adapter", lambda cli: called.append(cli) or None
    )
    screen = screening.create_screen(
        session,
        repo_id=repo_row.id,
        name="Codex-no-sandbox",
        system_prompt="P",
        cadence_cron="0 6 * * 1",
        cli="codex",
    )
    with pytest.raises(screening.ScreeningError, match="sandbox"):
        engine.run_screen(session, screen, force=True)
    assert called == []


def test_codex_screening_runs_when_sandbox_usable(
    session, repo_row, engine, monkeypatch
) -> None:
    """Sanity pair to the refusal test: a codex screening proceeds when the
    sandbox probe reports usable (i.e. bwrap user namespaces work)."""
    monkeypatch.setattr(screening, "_codex_sandbox_usable", lambda: True)
    _install_adapter(monkeypatch, FakeHandle(_done_events("[]")))
    screen = screening.create_screen(
        session,
        repo_id=repo_row.id,
        name="Codex-sandbox-ok",
        system_prompt="P",
        cadence_cron="0 6 * * 2",
        cli="codex",
    )
    run = engine.run_screen(session, screen, force=True)
    assert run is not None
    assert run.status == "done"


class HangProc:
    """A fake proc that the watchdog's ``_kill_proc`` can actually kill."""

    def __init__(self) -> None:
        self.killed = False

    def poll(self):
        return 0 if self.killed else None

    def wait(self, timeout=None) -> int:
        return 0

    def terminate(self) -> None:
        self.killed = True

    def kill(self) -> None:
        self.killed = True


class HangHandle:
    """A handle whose event stream never produces an event until the process is
    killed — used to prove the screening watchdog terminates a hung agent."""

    def __init__(self) -> None:
        self.proc = HangProc()
        self.session_id = "ses_hang"

    def events(self):
        while not self.proc.killed:
            time.sleep(0.05)
        return
        yield  # pragma: no cover - generator marker


def _error_events() -> list[AgentEvent]:
    return [AgentEvent(type="error", text="boom")]


def test_failed_run_is_retried_after_cooldown(session, repo_row, engine, monkeypatch):
    """A *failed* run is not a valid audit — after the cooldown the screen runs
    again at the same HEAD (it is not permanently silenced)."""
    _install_adapter(monkeypatch, FakeHandle(_error_events()))
    screen = _make_screen(session, repo_row)
    first = engine.run_screen(session, screen, force=True)
    assert first.status == "failed"
    # Backdate the failure past the retry cooldown.
    first.finished_at = now() - timedelta(hours=2)
    session.commit()
    _install_adapter(monkeypatch, FakeHandle(_done_events("[]")))
    second = engine.run_screen(session, screen)  # non-force
    assert second is not None
    assert second.id != first.id
    assert second.status == "done"


def test_recent_failed_run_suppressed_by_cooldown(session, repo_row, engine, monkeypatch):
    """A failure within the cooldown window is not hot-looped."""
    _install_adapter(monkeypatch, FakeHandle(_error_events()))
    screen = _make_screen(session, repo_row)
    engine.run_screen(session, screen, force=True)
    _install_adapter(monkeypatch, FakeHandle(_done_events("[]")))
    assert engine.run_screen(session, screen) is None


def test_screening_audit_env_has_no_token(session, repo_row, engine, monkeypatch):
    """Screening audits untrusted code — the agent env must never carry the PAT."""
    captured: dict[str, object] = {}

    class FakeAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["env"] = env
            return FakeHandle(_done_events("[]"))

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.screening.get_adapter", lambda cli: FakeAdapter())
    screen = _make_screen(session, repo_row)
    engine.run_screen(session, screen, force=True)
    env = captured["env"]
    assert isinstance(env, dict)
    # The key may exist as None (stripped at spawn) — but no token value may leak.
    assert env.get("JALEBI_GITHUB_TOKEN") is None
    assert env.get("GH_TOKEN") is None
    assert env.get("GITHUB_TOKEN") is None


def test_concurrent_run_refused(session, repo_row, engine):
    """A manual 'Run now' racing a scheduler tick on the same screen is refused."""
    screen = _make_screen(session, repo_row)
    lock = engine._locks.setdefault(screen.id, threading.Lock())
    lock.acquire()
    try:
        with pytest.raises(screening.ScreeningError, match="already running"):
            engine.run_screen(session, screen)
    finally:
        lock.release()


def test_screening_run_stalls_and_fails(session, repo_row, engine, monkeypatch):
    """A hung agent is killed by the watchdog; the run fails, the scheduler is
    not blocked, and the worktree is still cleaned up."""
    settings.set_setting(session, "stall_timeout_seconds", 2)
    _install_adapter(monkeypatch, HangHandle())
    screen = _make_screen(session, repo_row)
    run = engine.run_screen(session, screen, force=True)
    assert run is not None
    assert run.status == "failed"
    assert "no output" in (run.error or "")


def test_delete_screen_refuses_while_running(session, repo_row):
    screen = _make_screen(session, repo_row)
    session.add(ScreeningRun(screening_id=screen.id, head_sha="abc", status="running"))
    session.commit()
    with pytest.raises(screening.ScreeningError, match="still running"):
        screening.delete_screen(session, screen.id)
    # A screen with no in-flight run deletes normally.
    run = session.query(ScreeningRun).filter_by(screening_id=screen.id).one()
    run.status = "done"
    session.commit()
    assert screening.delete_screen(session, screen.id) is True


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
    ran = scheduler.tick(now_dt=now)
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
    ran = scheduler.tick(now_dt=datetime(2026, 8, 11, 8, 0))
    assert ran == 0


def test_parse_findings_strict_distinguishes_garbage_from_empty():
    """Strict parsing reports validity: garbage/truncation is not a clean audit."""
    findings, ok = screening.parse_findings_strict("here is my report: all good, no JSON")
    assert (findings, ok) == ([], False)
    findings, ok = screening.parse_findings_strict('[{"severity": "high", "title":')
    assert (findings, ok) == ([], False)
    findings, ok = screening.parse_findings_strict("")
    assert (findings, ok) == ([], False)
    findings, ok = screening.parse_findings_strict("[]")
    assert ok is True and findings == []
    findings, ok = screening.parse_findings_strict(
        '```json\n[{"severity": "low", "title": "T"}]\n```'
    )
    assert ok is True and len(findings) == 1
    # Lenient wrapper keeps its old contract.
    assert screening.parse_findings("no json here") == []


def test_garbage_output_fails_run_instead_of_clean_done(
    session, repo_row, engine, monkeypatch
):
    """Unparseable agent output must fail the run — a `done` would watermark the
    HEAD and silently suppress the next tick (false negative)."""
    _install_adapter(monkeypatch, FakeHandle(_done_events("prose without any array")))
    screen = _make_screen(session, repo_row)
    run = engine.run_screen(session, screen, force=True)
    assert run is not None
    assert run.status == "failed"
    assert "unparseable" in (run.error or "")


def test_reconcile_stale_runs(session, repo_row):
    """Startup recovery marks crash-orphaned non-terminal runs failed."""
    screen = _make_screen(session, repo_row)
    session.add_all(
        [
            ScreeningRun(screening_id=screen.id, head_sha="a", status="running"),
            ScreeningRun(screening_id=screen.id, head_sha="b", status="queued"),
            ScreeningRun(screening_id=screen.id, head_sha="c", status="done"),
        ]
    )
    session.commit()
    assert screening.reconcile_stale_runs(session) == 2
    rows = {
        r.head_sha: (r.status, r.error)
        for r in session.query(ScreeningRun).filter_by(screening_id=screen.id)
    }
    assert rows["a"][0] == "failed" and rows["a"][1]
    assert rows["b"][0] == "failed"
    assert rows["c"][0] == "done"


def test_run_screen_refuses_deleted_screen(session, repo_row, engine, monkeypatch):
    """The post-lock liveness re-check closes the delete-during-preflight race."""
    _install_adapter(monkeypatch, FakeHandle(_done_events("[]")))
    screen = _make_screen(session, repo_row)
    screen_id = screen.id
    session.delete(screen)
    session.commit()
    ghost = screening.get_screen(session, screen_id)
    assert ghost is None
    # Rebuild a detached stand-in carrying the deleted id.
    from jalebi.db import Screening

    standin = Screening(
        id=screen_id, repo_id=repo_row.id, name="G", system_prompt="P",
        cadence_cron="0 6 * * 1",
    )
    with pytest.raises(screening.ScreeningError, match="deleted"):
        engine.run_screen(session, standin)


def test_update_screen_null_clears_nullable_fields(session, repo_row):
    """Explicit null clears scope/branch/backend/model pins; omitted keys stay."""
    screen = screening.create_screen(
        session, repo_id=repo_row.id, name="S", system_prompt="P",
        cadence_cron="0 6 * * 1", scope_branch="main", cli="opencode",
        model="m-1",
    )
    screening.update_screen(session, screen, system_prompt="P2")
    assert screen.scope_branch == "main"  # omitted → kept
    assert screen.model == "m-1"
    screening.update_screen(session, screen, scope_branch=None, model=None)
    assert screen.scope_branch is None
    assert screen.model is None


def test_update_screen_cli_change_drops_stale_model(session, repo_row):
    """Switching backend without a new model drops the old pin (it belonged to
    the old backend); an explicit new model in the same call is kept."""
    screen = screening.create_screen(
        session, repo_id=repo_row.id, name="S", system_prompt="P",
        cadence_cron="0 6 * * 1", cli="opencode", model="m-1",
    )
    screening.update_screen(session, screen, cli="claude")
    assert screen.cli == "claude"
    assert screen.model is None
    screening.update_screen(session, screen, cli="opencode", model="m-2")
    assert (screen.cli, screen.model) == ("opencode", "m-2")


def test_finding_file_path_capped():
    """Untrusted `file` values are capped before they can reach task prompts."""
    finding = screening._normalize_finding(
        {"severity": "high", "title": "T", "file": "x" * 2000}
    )
    assert finding is not None and len(finding["file"]) == 500


def test_parse_strict_rejects_non_finding_arrays():
    """A valid JSON array with zero usable findings is garbage, not clean."""
    findings, ok = screening.parse_findings_strict('[{"error": "rate limited"}]')
    assert (findings, ok) == ([], False)
    findings, ok = screening.parse_findings_strict('["just a string", 42]')
    assert (findings, ok) == ([], False)
    findings, ok = screening.parse_findings_strict(
        '[{"severity": "high", "title": "Real"}, {"bogus": 1}]'
    )
    assert ok is True and len(findings) == 1


def test_scheduler_tick_records_preflight_failure(session, repo_row, app):
    """Scheduled preflight failures land in run history, not just the log."""
    from jalebi.db import ScreeningRun

    screen = _make_screen(session, repo_row, cadence_cron="* * * * *")
    repo_row.connected = False
    session.commit()
    scheduler = screening.ScreeningScheduler(app.config["JALEBI_CONFIG"])
    assert scheduler.tick() == 0
    rows = session.query(ScreeningRun).filter_by(screening_id=screen.id).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "not connected" in (rows[0].error or "")
