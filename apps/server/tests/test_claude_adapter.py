"""Claude Code adapter tests (PRD F4) — parse mapping, commands, integration."""

import io

from jalebi.adapters.claude import CLAUDE_CURATED, NO_AUTH_RESULT, ClaudeAdapter
from jalebi.adapters.types import RunHandle

adapter = ClaudeAdapter()

SESSION_ID = "22222222-3333-4444-5555-666666666666"

INIT = (
    '{"type":"system","subtype":"init","session_id":"' + SESSION_ID + '",'
    '"model":"openai/qwen/qwen3-coder-next","permissionMode":"default",'
    '"apiKeySource":"authToken","tools":["Bash","Write"],"slash_commands":[],"skills":[]}'
)
API_RETRY = '{"type":"system","subtype":"api_retry","attempt":1,"max_retries":3}'
AUTH_STATUS = '{"type":"system","subtype":"auth_status","state":"disconnected"}'
USER_TOOL_RESULT = (
    '{"type":"user","session_id":"' + SESSION_ID + '","message":{"content":['
    '{"type":"tool_result","tool_use_id":"toolu_01","content":['
    '{"type":"text","text":"<output>"}],"is_error":false}]}}'
)
USER_ECHO = (
    '{"type":"user","session_id":"' + SESSION_ID + '",'
    '"message":{"content":[{"type":"text","text":"do it"}]}}'
)
ASSISTANT_TEXT = (
    '{"type":"assistant","session_id":"' + SESSION_ID + '","message":'
    '{"id":"msg_01","content":[{"type":"text","text":"Let me check."}]}}'
)
ASSISTANT_TOOL_USE = (
    '{"type":"assistant","session_id":"' + SESSION_ID + '","message":{"content":['
    '{"type":"tool_use","id":"toolu_02","name":"Bash","input":{"command":"ls -la"}}]}}'
)
ASSISTANT_THINKING = (
    '{"type":"assistant","session_id":"' + SESSION_ID + '","message":{"content":['
    '{"type":"thinking","thinking":"..."}]}}'
)
ASSISTANT_MIXED = (
    '{"type":"assistant","session_id":"' + SESSION_ID + '","message":{"content":['
    '{"type":"text","text":"Running."},'
    '{"type":"tool_use","id":"toolu_03","name":"Bash","input":{"command":"pwd"}},'
    '{"type":"thinking","thinking":"..."}]}}'
)
RESULT_SUCCESS = (
    '{"type":"result","subtype":"success","is_error":false,"result":"Done.",'
    '"session_id":"' + SESSION_ID + '"}'
)
RESULT_SUCCESS_EMPTY = (
    '{"type":"result","subtype":"success","is_error":false,"result":"",'
    '"session_id":"' + SESSION_ID + '"}'
)
RESULT_NO_AUTH = (
    '{"type":"result","subtype":"success","is_error":true,"terminal_reason":"api_error",'
    '"result":"Not logged in \\u00b7 Please run /login","session_id":"' + SESSION_ID + '"}'
)
RESULT_ERROR_SUBTYPE = (
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"errors":["Command failed","Retry limit exceeded"],"result":""}'
)
RESULT_BARE_ERROR = '{"type":"result","is_error":true}'


class FakeProc:
    def __init__(self, out: str = "", err: str = "", code: int = 0):
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)
        self._code = code

    def poll(self):
        return None

    def wait(self) -> int:
        return self._code


def test_parse_init_is_step_and_captures_session_id() -> None:
    events = adapter.parse(INIT)
    assert len(events) == 1
    assert events[0].type == "step"
    assert events[0].session_id == SESSION_ID


def test_parse_init_never_echoes_payload() -> None:
    events = adapter.parse(INIT)
    assert events[0].text is None
    data = events[0].data
    assert data is not None
    assert data == {"session_id": SESSION_ID}


def test_parse_system_other_subtypes_silent() -> None:
    assert adapter.parse(API_RETRY) == []
    assert adapter.parse(AUTH_STATUS) == []


def test_parse_user_tool_result_is_tool_call() -> None:
    events = adapter.parse(USER_TOOL_RESULT)
    assert events[0].type == "tool_call"
    data = events[0].data
    assert data is not None
    assert data == {
        "tool": "tool_result",
        "tool_use_id": "toolu_01",
        "output": "<output>",
        "is_error": False,
    }


def test_parse_user_prompt_echo_silent() -> None:
    assert adapter.parse(USER_ECHO) == []


def test_parse_assistant_text_is_message() -> None:
    events = adapter.parse(ASSISTANT_TEXT)
    assert events[0].type == "message"
    assert events[0].text == "Let me check."


def test_parse_assistant_tool_use_is_tool_call() -> None:
    events = adapter.parse(ASSISTANT_TOOL_USE)
    assert events[0].type == "tool_call"
    data = events[0].data
    assert data is not None
    assert data == {
        "tool": "Bash",
        "input": {"command": "ls -la"},
        "tool_use_id": "toolu_02",
    }


def test_parse_assistant_thinking_silent() -> None:
    assert adapter.parse(ASSISTANT_THINKING) == []


def test_parse_assistant_mixed_blocks_in_order() -> None:
    events = adapter.parse(ASSISTANT_MIXED)
    assert [e.type for e in events] == ["message", "tool_call"]
    assert events[0].text == "Running."
    tool_data = events[1].data
    assert tool_data is not None
    assert tool_data["tool"] == "Bash"


def test_parse_result_success_is_final_message() -> None:
    events = adapter.parse(RESULT_SUCCESS)
    assert events[0].type == "message"
    assert events[0].text == "Done."


def test_parse_result_success_empty_silent() -> None:
    assert adapter.parse(RESULT_SUCCESS_EMPTY) == []


def test_parse_result_no_auth_is_error_verbatim() -> None:
    """is_error is the discriminator, not the subtype — the no-auth failure
    keeps subtype:"success"."""
    events = adapter.parse(RESULT_NO_AUTH)
    assert events[0].type == "error"
    assert events[0].text == NO_AUTH_RESULT


def test_parse_result_error_subtype_uses_errors() -> None:
    events = adapter.parse(RESULT_ERROR_SUBTYPE)
    assert events[0].type == "error"
    assert events[0].text == "Command failed\nRetry limit exceeded"


def test_parse_result_bare_error_falls_back() -> None:
    events = adapter.parse(RESULT_BARE_ERROR)
    assert events[0].type == "error"
    assert events[0].text


def test_parse_result_error_terminal_reason_fallback() -> None:
    line = (
        '{"type":"result","subtype":"error_max_budget_usd","is_error":true,'
        '"result":"","errors":null,"terminal_reason":"max_budget"}'
    )
    events = adapter.parse(line)
    assert events[0].type == "error"
    assert events[0].text == "max_budget"


def test_parse_init_without_session_id_does_not_crash() -> None:
    events = adapter.parse('{"type":"system","subtype":"init","model":"m"}')
    assert events[0].type == "step"
    assert events[0].session_id is None


def test_parse_non_json_verbatim() -> None:
    events = adapter.parse("not json at all")
    assert events[0].type == "message"
    assert events[0].text == "not json at all"


def test_parse_unknown_type_verbatim() -> None:
    line = '{"type":"mystery"}'
    events = adapter.parse(line)
    assert events[0].type == "message"
    assert events[0].text == line


def test_parse_malformed_payloads_never_crash() -> None:
    assert adapter.parse('{"type":"assistant","message":[]}') == []
    assert adapter.parse('{"type":"assistant","message":{"content":"oops"}}') == []
    assert adapter.parse('{"type":"user","message":null}') == []
    assert adapter.parse('{"type":"result","is_error":false}') == []
    events = adapter.parse("[1,2,3]")
    assert events[0].type == "message"


def test_list_models_curated() -> None:
    assert adapter.list_models() == CLAUDE_CURATED


def _capture_spawn(monkeypatch):
    from jalebi.adapters import claude as claude_mod

    captured: list[list[str]] = []

    def fake_spawn(args, cwd, env=None):
        captured.append(list(args))
        return FakeProc()

    monkeypatch.setattr(claude_mod, "_spawn", fake_spawn)
    return captured


def test_start_command_construction(monkeypatch) -> None:
    captured = _capture_spawn(monkeypatch)
    handle = adapter.start("/ws", "do it", model="sonnet")
    args = captured[0]
    assert args[0].endswith("claude")
    assert args[1] == "-p"
    assert args[2] == "do it"
    assert "--output-format" in args and "stream-json" in args
    assert "--verbose" in args
    assert "--permission-mode" in args and "bypassPermissions" in args
    assert args[args.index("--model") + 1] == "sonnet"
    assert handle.name == "claude"


def test_start_no_model(monkeypatch) -> None:
    captured = _capture_spawn(monkeypatch)
    adapter.start("/ws", "do it")
    args = captured[0]
    assert "--model" not in args
    assert "--verbose" in args
    assert "--permission-mode" in args


def test_resume_command_construction(monkeypatch) -> None:
    captured = _capture_spawn(monkeypatch)
    handle = adapter.resume("/ws", SESSION_ID, "continue", model="opus")
    args = captured[0]
    assert args[args.index("--resume") + 1] == SESSION_ID
    assert "--output-format" in args and "stream-json" in args
    assert "--verbose" in args
    assert "--permission-mode" in args and "bypassPermissions" in args
    assert args[args.index("--model") + 1] == "opus"
    assert handle.name == "claude"


def test_resume_no_model(monkeypatch) -> None:
    captured = _capture_spawn(monkeypatch)
    adapter.resume("/ws", SESSION_ID, "continue")
    args = captured[0]
    assert "--model" not in args
    assert args[args.index("--resume") + 1] == SESSION_ID


def test_spawn_wraps_in_bash_dash_c(monkeypatch) -> None:
    from jalebi.adapters import claude as claude_mod

    captured: dict = {}

    class FakeProc:
        stdout = None
        stderr = None

        def __init__(self, **kw):
            captured.update(kw)

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return FakeProc(**kwargs)

    monkeypatch.setattr(claude_mod.subprocess, "Popen", fake_popen)
    claude_mod._spawn(["/some/claude", "-p", "hi"], "/some/ws", {"A": "1", "B": None})
    args = captured["args"]
    assert args[0] == "/bin/bash"
    assert args[1] == "-c"
    assert args[2].startswith("cd /some/ws && exec /some/claude")
    assert captured["start_new_session"] is True
    assert "A" in captured["env"] and "B" not in captured["env"]


def test_runhandle_streams_claude_events_and_session() -> None:
    out = f"{INIT}\n{ASSISTANT_TEXT}\n{RESULT_SUCCESS}\n"
    handle = RunHandle(proc=FakeProc(out=out, code=0), parse=adapter.parse, name="claude")
    events = list(handle.events())
    assert [e.type for e in events] == ["step", "message", "message", "done"]
    assert handle.session_id == SESSION_ID


def test_runhandle_no_auth_result_surfaces_error() -> None:
    out = f"{INIT}\n{RESULT_NO_AUTH}\n"
    handle = RunHandle(proc=FakeProc(out=out, code=1), parse=adapter.parse, name="claude")
    events = list(handle.events())
    types = [e.type for e in events]
    assert "step" in types and "error" in types
    error_texts = [e.text for e in events if e.type == "error"]
    assert NO_AUTH_RESULT in error_texts
    assert any(t and t.startswith("claude exited with code 1") for t in error_texts)


def test_runhandle_error_text_uses_claude_name() -> None:
    handle = RunHandle(
        proc=FakeProc(out="", err="boom\n", code=3), parse=adapter.parse, name="claude"
    )
    text = list(handle.events())[-1].text
    assert text is not None
    assert text.startswith("claude exited with code 3")
    assert "boom" in text
