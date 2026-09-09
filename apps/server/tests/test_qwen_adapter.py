"""qwen (Qwen Code) CLI adapter tests (PRD F4). Fixture lines mirror the live
``qwen -p … -o stream-json --yolo`` smoke run (qwen 0.23.1, Sep 2026)."""

import io

from jalebi.adapters.qwen import QwenAdapter
from jalebi.adapters.types import RunHandle

adapter = QwenAdapter()

SESSION_ID = "1b8020f3-413c-4e7c-b499-e557e35847f2"

INIT = (
    '{"type":"system","subtype":"init","session_id":"' + SESSION_ID + '",'
    '"cwd":"/tmp/opencode/qwen-val","model":"inclusionai/ling-3.0-flash-fin:free",'
    '"permission_mode":"yolo","tools":["read"],"mcp_servers":[]}'
)
GOAL = (
    '{"type":"stream_event","session_id":"' + SESSION_ID + '",'
    '"event":{"type":"goal_state","goal_state":{"v":2,"goal":null,"activity":"idle"}}}'
)
ASSISTANT_THINKING = (
    '{"type":"assistant","session_id":"' + SESSION_ID + '",'
    '"message":{"role":"assistant","content":['
    '{"type":"thinking","thinking":"wants OK"}]}}'
)
ASSISTANT_TEXT = (
    '{"type":"assistant","session_id":"' + SESSION_ID + '",'
    '"message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}'
)
ASSISTANT_TOOL_USE = (
    '{"type":"assistant","session_id":"' + SESSION_ID + '",'
    '"message":{"role":"assistant","content":['
    '{"type":"tool_use","id":"toolu_1","name":"Bash","input":{"command":"ls"}}]}}'
)
USER_TOOL_RESULT = (
    '{"type":"user","session_id":"' + SESSION_ID + '",'
    '"message":{"role":"user","content":['
    '{"type":"tool_result","tool_use_id":"toolu_1","content":[{"type":"text","text":"x"}]}]}}'
)
USER_ECHO = (
    '{"type":"user","session_id":"' + SESSION_ID + '",'
    '"message":{"role":"user","content":[{"type":"text","text":"do it"}]}}'
)
RESULT_SUCCESS = (
    '{"type":"result","subtype":"success","session_id":"' + SESSION_ID + '",'
    '"is_error":false,"result":"OK","num_turns":1}'
)
RESULT_SUCCESS_EMPTY = (
    '{"type":"result","subtype":"success","session_id":"' + SESSION_ID + '",'
    '"is_error":false,"result":""}'
)
RESULT_ERROR = (
    '{"type":"result","subtype":"error_during_execution","session_id":"' + SESSION_ID + '",'
    '"is_error":true,"error":{"message":"kaput"},"result":""}'
)


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
    assert events[0].text is None


def test_parse_goal_state_is_silent() -> None:
    assert adapter.parse(GOAL) == []


def test_parse_thinking_is_silent() -> None:
    assert adapter.parse(ASSISTANT_THINKING) == []


def test_parse_text_is_message() -> None:
    events = adapter.parse(ASSISTANT_TEXT)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"


def test_parse_tool_use() -> None:
    events = adapter.parse(ASSISTANT_TOOL_USE)
    assert len(events) == 1
    assert events[0].type == "tool_call"
    assert events[0].data is not None
    assert events[0].data["tool"] == "Bash"


def test_parse_user_tool_result_visible_prompt_echo_silent() -> None:
    result = adapter.parse(USER_TOOL_RESULT)
    assert len(result) == 1
    assert result[0].type == "tool_call"
    assert result[0].data is not None
    assert result[0].data["output"] == "x"
    assert adapter.parse(USER_ECHO) == []


def test_parse_result_success_emits_final_text() -> None:
    events = adapter.parse(RESULT_SUCCESS)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"


def test_parse_result_empty_is_silent() -> None:
    assert adapter.parse(RESULT_SUCCESS_EMPTY) == []


def test_parse_result_error() -> None:
    events = adapter.parse(RESULT_ERROR)
    assert events[0].type == "error"
    assert events[0].text == "kaput"


def test_runhandle_captures_session_id_from_init() -> None:
    handle = RunHandle(proc=FakeProc(out=INIT + "\n"), parse=adapter.parse, name="qwen")
    list(handle.events())
    assert handle.session_id == SESSION_ID


def _capture_spawn(monkeypatch):
    """Replace qwen._spawn with a recorder; returns the captured argv list."""
    import jalebi.adapters.qwen as qwen_module

    captured: list[list[str]] = []
    monkeypatch.setattr(
        qwen_module, "_spawn", lambda args, cwd, env=None: captured.append(args) or FakeProc()
    )
    monkeypatch.setattr(qwen_module, "_binary", lambda: "qwen")
    return captured


def test_argv_has_no_cli_wall_clock_cap(monkeypatch) -> None:
    # The queue's per-task timeout + stall watchdog own the deadline — the
    # adapter must not impose its own (previously a fixed 600s).
    captured = _capture_spawn(monkeypatch)
    adapter.start("/tmp/ws/t1", "go")
    assert captured, "start must spawn"
    assert "--max-wall-time" not in captured[0]
    adapter.resume("/tmp/ws/t1", SESSION_ID, "go")
    assert "--max-wall-time" not in captured[1]
    assert captured[1][-4:-2] == ["-r", SESSION_ID] or "-r" in captured[1]
