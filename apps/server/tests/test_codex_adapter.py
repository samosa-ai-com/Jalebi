"""Codex CLI adapter tests (PRD F4) — parse mapping, sandbox, models, commands."""

import io
import json

from jalebi.adapters.codex import CODE_X_CURATED, CodexAdapter
from jalebi.adapters.types import RunHandle

adapter = CodexAdapter()

THREAD_ID = "11111111-2222-3333-4444-555555555555"
THREAD_STARTED = f'{{"type":"thread.started","thread_id":"{THREAD_ID}"}}'
NOTICE_ITEM = (
    '{"type":"item.completed","item":{"id":"item_0","type":"error",'
    '"message":"Skill descriptions were shortened to fit the skills context budget."}}'
)
AGENT_MSG = (
    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"OK"}}'
)
CMD_EXEC = (
    '{"type":"item.completed","item":{"id":"item_2","type":"command_execution",'
    '"command":"pwd","aggregated_output":"/ws\\n","exit_code":0,"status":"completed"}}'
)
FILE_CHANGE = (
    '{"type":"item.completed","item":{"id":"item_3","type":"file_change",'
    '"changes":[{"path":"a.py","kind":"modified"}],"status":"completed"}}'
)
TURN_FAILED = (
    '{"type":"turn.failed","error":{"message":"{\\"code\\":\\"invalid_request_error\\",'
    '\\"message\\":\\"400 model not found\\"}"}}'
)
TOP_ERROR = '{"type":"error","message":"Model metadata for gpt-4.1-mini not found"}'
TURN_STARTED = '{"type":"turn.started"}'
ITEM_STARTED = '{"type":"item.started","item":{"id":"item_0","type":"reasoning"}}'


class FakeProc:
    def __init__(self, out: str = "", err: str = "", code: int = 0):
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)
        self._code = code

    def poll(self):
        return None

    def wait(self) -> int:
        return self._code


def test_parse_thread_started_captures_session_id() -> None:
    events = adapter.parse(THREAD_STARTED)
    assert len(events) == 1
    assert events[0].type == "step"
    assert events[0].session_id == THREAD_ID


def test_parse_notice_errors_are_message_not_error() -> None:
    """Notices (item-level and top-level ``error``) must not end the run."""
    assert adapter.parse(NOTICE_ITEM)[0].type == "message"
    assert adapter.parse(TOP_ERROR)[0].type == "message"


def test_parse_agent_message() -> None:
    events = adapter.parse(AGENT_MSG)
    assert events[0].type == "message"
    assert events[0].text == "OK"


def test_parse_command_execution_is_tool_call() -> None:
    events = adapter.parse(CMD_EXEC)
    assert events[0].type == "tool_call"
    data = events[0].data or {}
    assert data["command"] == "pwd"
    assert data["output"] == "/ws\n"
    assert data["exit_code"] == 0
    assert data["status"] == "completed"


def test_parse_file_change_is_tool_call() -> None:
    events = adapter.parse(FILE_CHANGE)
    assert events[0].type == "tool_call"
    data = events[0].data or {}
    assert data["changes"] == [{"path": "a.py", "kind": "modified"}]


def test_parse_turn_failed_is_error_and_unwraps_json() -> None:
    events = adapter.parse(TURN_FAILED)
    assert events[0].type == "error"
    assert events[0].text == "400 model not found"


def test_parse_turn_started_and_item_started_silent() -> None:
    assert adapter.parse(TURN_STARTED) == []
    assert adapter.parse(ITEM_STARTED) == []
    assert adapter.parse('{"type":"turn.completed","usage":{}}') == []
    assert adapter.parse('{"type":"item.updated","item":{}}') == []


def test_parse_non_json_verbatim() -> None:
    events = adapter.parse("not json at all")
    assert events[0].type == "message"
    assert events[0].text == "not json at all"


def test_parse_unknown_type_verbatim() -> None:
    line = '{"type":"mystery"}'
    events = adapter.parse(line)
    assert events[0].type == "message"
    assert events[0].text == line


def test_parse_mcp_tool_call_is_tool_call() -> None:
    line = (
        '{"type":"item.completed","item":{"id":"item_5","type":"mcp_tool_call",'
        '"server":"github","tool":"get_issue","arguments":{"n":1},'
        '"result":"ok","status":"completed"}}'
    )
    events = adapter.parse(line)
    assert events[0].type == "tool_call"
    data = events[0].data or {}
    assert data["server"] == "github"
    assert data["tool_name"] == "get_issue"
    assert data["status"] == "completed"


def test_parse_unknown_subtype_with_text_falls_back_to_message() -> None:
    line = '{"type":"item.completed","item":{"id":"item_6","type":"widget","text":"hi"}}'
    events = adapter.parse(line)
    assert events[0].type == "message"
    assert events[0].text == "hi"


def test_parse_top_level_error_without_message_verbatim() -> None:
    line = '{"type":"error"}'
    events = adapter.parse(line)
    assert events[0].type == "message"
    assert events[0].text == line


def test_parse_non_dict_item_and_error_never_crash() -> None:
    assert adapter.parse('{"type":"item.completed","item":[1,2,3]}')[0].type == "message"
    assert adapter.parse('{"type":"turn.failed","error":"boom"}')[0].type == "error"


def test_list_models_from_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-5.6-terra", "visibility": "list"},
                    {"slug": "codex-auto-review", "visibility": "hide"},
                    {"slug": "gpt-5.4-mini", "visibility": "list"},
                ]
            }
        )
    )
    assert adapter.list_models() == ["gpt-5.4-mini", "gpt-5.6-terra"]


def test_list_models_cache_missing_falls_back_curated(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert adapter.list_models() == sorted(CODE_X_CURATED)


def test_list_models_corrupt_cache_falls_back(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text("{ not json")
    assert adapter.list_models() == sorted(CODE_X_CURATED)


def test_list_models_array_shaped_cache_falls_back(monkeypatch, tmp_path) -> None:
    """A valid-JSON-but-wrong-shape cache (array) must never crash."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text("[1,2,3]")
    assert adapter.list_models() == sorted(CODE_X_CURATED)


class FakeResult:
    def __init__(self, code: int):
        self.returncode = code


def _probe_subprocess(returncode: int):
    def fake_run(*args, **kwargs):
        return FakeResult(returncode)

    return fake_run


def test_sandbox_usable_true(monkeypatch) -> None:
    from jalebi.adapters import codex as codex_mod

    codex_mod._sandbox_usable.cache_clear()
    monkeypatch.setattr(codex_mod.subprocess, "run", _probe_subprocess(0))
    assert codex_mod._sandbox_usable() is True


def test_sandbox_usable_false(monkeypatch) -> None:
    from jalebi.adapters import codex as codex_mod

    codex_mod._sandbox_usable.cache_clear()
    monkeypatch.setattr(codex_mod.subprocess, "run", _probe_subprocess(1))
    assert codex_mod._sandbox_usable() is False


def test_sandbox_usable_no_bwrap(monkeypatch) -> None:
    from jalebi.adapters import codex as codex_mod

    codex_mod._sandbox_usable.cache_clear()
    monkeypatch.setattr(codex_mod.shutil, "which", lambda _name: None)
    assert codex_mod._sandbox_usable() is False


def test_sandbox_config_args_usable(monkeypatch) -> None:
    from jalebi.adapters import codex as codex_mod

    codex_mod._sandbox_usable.cache_clear()
    monkeypatch.setattr(codex_mod.subprocess, "run", _probe_subprocess(0))
    assert codex_mod._sandbox_config_args() == [
        "-c", 'sandbox_mode="workspace-write"',
        "-c", "sandbox_workspace_write.network_access=true",
    ]


def test_sandbox_config_args_unusable(monkeypatch) -> None:
    from jalebi.adapters import codex as codex_mod

    codex_mod._sandbox_usable.cache_clear()
    monkeypatch.setattr(codex_mod.subprocess, "run", _probe_subprocess(1))
    assert codex_mod._sandbox_config_args() == ["-c", 'sandbox_mode="danger-full-access"']


def _capture_spawn(monkeypatch) -> list[list[str]]:
    from jalebi.adapters import codex as codex_mod

    captured: list[list[str]] = []

    def fake_spawn(args, cwd, env=None):
        captured.append(list(args))
        return FakeProc()

    monkeypatch.setattr(codex_mod, "_spawn", fake_spawn)
    return captured


def test_start_command_construction(monkeypatch) -> None:
    captured = _capture_spawn(monkeypatch)
    handle = adapter.start("/ws", "do it", model="gpt-5.4-mini")
    args = captured[0]
    assert "exec" in args
    assert "--json" in args
    assert args[args.index("-m") + 1] == "gpt-5.4-mini"
    assert args[-1] == "do it"
    assert "-c" in args
    assert handle.name == "codex"


def test_start_no_model(monkeypatch) -> None:
    captured = _capture_spawn(monkeypatch)
    adapter.start("/ws", "do it")
    assert "-m" not in captured[0]


def test_resume_command_construction(monkeypatch) -> None:
    captured = _capture_spawn(monkeypatch)
    handle = adapter.resume("/ws", THREAD_ID, "continue", model="gpt-5.4-mini")
    args = captured[0]
    assert "resume" in args
    assert THREAD_ID in args
    assert "--json" in args
    assert args[args.index("-m") + 1] == "gpt-5.4-mini"
    assert args[-1] == "continue"
    assert "-c" in args
    assert handle.name == "codex"


def test_spawn_wraps_in_bash_dash_c(monkeypatch) -> None:
    from jalebi.adapters import codex as codex_mod

    captured: dict = {}

    class FakeProc:
        stdout = None
        stderr = None

        def __init__(self, **kw):
            captured.update(kw)

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return FakeProc(**kwargs)

    monkeypatch.setattr(codex_mod.subprocess, "Popen", fake_popen)
    codex_mod._spawn(["/bin/codex", "exec", "--json", "hi"], "/some/ws", {})
    args = captured["args"]
    assert args[0] == "/bin/bash"
    assert args[1] == "-c"
    cmd = args[2]
    assert cmd.startswith("cd /some/ws && exec /bin/codex")
    assert captured["start_new_session"] is True


def test_runhandle_streams_codex_events_and_session() -> None:
    out = f"{THREAD_STARTED}\n{AGENT_MSG}\n"
    handle = RunHandle(proc=FakeProc(out=out, code=0), parse=adapter.parse, name="codex")
    events = list(handle.events())
    assert [e.type for e in events] == ["step", "message", "done"]
    assert handle.session_id == THREAD_ID


def test_runhandle_error_text_uses_codex_name() -> None:
    proc = FakeProc(out="", err="boom\n", code=3)
    handle = RunHandle(proc=proc, parse=adapter.parse, name="codex")
    text = list(handle.events())[-1].text
    assert text is not None
    assert text.startswith("codex exited with code 3")
    assert "boom" in text
