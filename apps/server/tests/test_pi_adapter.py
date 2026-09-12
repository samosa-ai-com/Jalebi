"""pi CLI adapter tests (PRD F4) — parse mapping, commands. Fixture lines mirror
the live ``pi --print --mode json`` smoke run (pi 0.80.2, Sep 2026)."""

import io

from jalebi.adapters.pi import PI_DEFAULT_MODEL, PiAdapter
from jalebi.adapters.types import RunHandle

adapter = PiAdapter()

SESSION_ID = "01a08268-cf3b-7c17-9c3c-0704d9ad274b"

SESSION = (
    '{"type":"session","version":3,"id":"' + SESSION_ID + '",'
    '"timestamp":"2026-09-08T19:00:00.000Z","cwd":"/tmp/opencode/pi-val"}'
)
AGENT_START = '{"type":"agent_start","sessionId":"' + SESSION_ID + '"}'
TURN_START = '{"type":"turn_start","sessionId":"' + SESSION_ID + '"}'
MESSAGE_START = (
    '{"type":"message_start","sessionId":"' + SESSION_ID + '",'
    '"message":{"role":"assistant"}}'
)
MESSAGE_UPDATE_DELTA = (
    '{"type":"message_update","sessionId":"' + SESSION_ID + '",'
    '"assistantMessageEvent":{"type":"text_delta","delta":"O"}}'
)
MESSAGE_END_OK = (
    '{"type":"message_end","sessionId":"' + SESSION_ID + '",'
    '"message":{"role":"assistant","stopReason":"stop","content":['
    '{"type":"thinking","thinking":"user wants OK"},'
    '{"type":"text","text":"OK"}]}}'
)
MESSAGE_END_TOOLCALL = (
    '{"type":"message_end","sessionId":"' + SESSION_ID + '",'
    '"message":{"role":"assistant","stopReason":"stop","content":['
    '{"type":"toolcall_start","id":"call_1","toolName":"read","input":{"path":"x"}}]}}'
)
MESSAGE_END_MODEL_ERROR = (
    '{"type":"message_end","sessionId":"' + SESSION_ID + '",'
    '"message":{"role":"assistant","stopReason":"error",'
    '"errorMessage":"404 This model is unavailable for free.","content":[]}}'
)
TOOL_EXEC_START = (
    '{"type":"tool_execution_start","sessionId":"' + SESSION_ID + '","toolName":"read"}'
)
TOOL_EXEC_END = (
    '{"type":"tool_execution_end","sessionId":"' + SESSION_ID + '","toolName":"read"}'
)
AGENT_END_OK = (
    '{"type":"agent_end","sessionId":"' + SESSION_ID + '","stopReason":"stop",'
    '"willRetry":false}'
)
AGENT_END_ERROR = (
    '{"type":"agent_end","sessionId":"' + SESSION_ID + '","stopReason":"error",'
    '"errorMessage":"boom","willRetry":false}'
)
TURN_END_ERROR = (
    '{"type":"turn_end","sessionId":"' + SESSION_ID + '","stopReason":"error",'
    '"errorMessage":"boom"}'
)
ERROR_LINE = (
    '{"type":"error","sessionId":"' + SESSION_ID + '","errorMessage":"kaput"}'
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


def test_parse_session_is_step_and_captures_id() -> None:
    events = adapter.parse(SESSION)
    assert len(events) == 1
    assert events[0].type == "step"
    assert events[0].session_id == SESSION_ID
    assert events[0].text is None


def test_parse_lifecycle_lines_are_steps() -> None:
    for line in (AGENT_START, TURN_START):
        events = adapter.parse(line)
        assert len(events) == 1
        assert events[0].type == "step"
        assert events[0].session_id == SESSION_ID


def test_parse_deltas_are_silent() -> None:
    assert adapter.parse(MESSAGE_START) == []
    assert adapter.parse(MESSAGE_UPDATE_DELTA) == []


def test_parse_message_end_emits_text_not_thinking() -> None:
    events = adapter.parse(MESSAGE_END_OK)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"


def test_parse_message_end_toolcall_block() -> None:
    events = adapter.parse(MESSAGE_END_TOOLCALL)
    assert len(events) == 1
    assert events[0].type == "tool_call"
    assert events[0].data is not None
    assert events[0].data["tool"] == "read"


def test_parse_model_error_despite_exit_zero() -> None:
    # pi exits 0 on model failures — stopReason:"error" is the signal.
    events = adapter.parse(MESSAGE_END_MODEL_ERROR)
    assert len(events) == 1
    assert events[0].type == "error"
    assert "unavailable for free" in (events[0].text or "")


def test_parse_agent_end_error() -> None:
    events = adapter.parse(AGENT_END_ERROR)
    assert events[0].type == "error"
    assert events[0].text == "boom"


def test_parse_agent_end_ok_is_silent() -> None:
    assert adapter.parse(AGENT_END_OK) == []


def test_parse_turn_end_error() -> None:
    events = adapter.parse(TURN_END_ERROR)
    assert events[0].type == "error"


def test_parse_tool_execution_markers() -> None:
    start = adapter.parse(TOOL_EXEC_START)
    assert start[0].type == "tool_call"
    assert start[0].data is not None
    assert start[0].data["tool"] == "read"
    end = adapter.parse(TOOL_EXEC_END)
    assert end[0].type == "step"


def test_parse_error_line() -> None:
    events = adapter.parse(ERROR_LINE)
    assert events[0].type == "error"
    assert events[0].text == "kaput"


def test_parse_non_json_verbatim() -> None:
    events = adapter.parse("plain banner line")
    assert events[0].type == "message"
    assert events[0].text == "plain banner line"


def test_runhandle_captures_session_id_from_header() -> None:
    handle = RunHandle(proc=FakeProc(out=SESSION + "\n"), parse=adapter.parse, name="pi")
    list(handle.events())
    assert handle.session_id == SESSION_ID


def test_default_model_constant_documented() -> None:
    assert PI_DEFAULT_MODEL == "openrouter/free"
