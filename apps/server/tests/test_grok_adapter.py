"""grok (Grok Build) CLI adapter tests (PRD F4). Fixture lines mirror the live
``grok -p … --output-format streaming-json`` smoke run (grok 1.0.13,
Sep 2026)."""

import io

from jalebi.adapters.grok import GrokAdapter
from jalebi.adapters.types import RunHandle

adapter = GrokAdapter()

SESSION_ID = "01a08267-eb2c-7780-9b42-3c58e5c4f7fb"

INIT = (
    '{"type":"available_commands","tools":["run_terminal_command","read_file"],'
    '"commands":["compact"]}'
)
THOUGHT = '{"type":"thought","data":" user"}'
TEXT = '{"type":"text","data":"OK"}'
TOOL_CALL = (
    '{"type":"tool_call","toolCallId":"call_1","toolName":"read_file",'
    '"kind":"read","status":"in_progress","rawInput":"{\\"path\\":\\"x\\"}"}'
)
TOOL_UPDATE = (
    '{"type":"tool_call_update","toolCallId":"call_1","status":"completed",'
    '"rawOutput":"y"}'
)
USAGE = '{"type":"usage","usage":{"input_tokens":17640,"output_tokens":30}}'
END_OK = (
    '{"type":"end","stopReason":"end_turn","sessionId":"' + SESSION_ID + '",'
    '"num_turns":1,"total_cost_usd":0.035524}'
)
END_REFUSAL = (
    '{"type":"end","stopReason":"refusal","sessionId":"' + SESSION_ID + '",'
    '"num_turns":1}'
)
ERROR_LINE = '{"type":"error","message":"kaput"}'


class FakeProc:
    def __init__(self, out: str = "", err: str = "", code: int = 0):
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)
        self._code = code

    def poll(self):
        return None

    def wait(self) -> int:
        return self._code


def test_parse_init_is_step() -> None:
    events = adapter.parse(INIT)
    assert len(events) == 1
    assert events[0].type == "step"


def test_parse_thought_and_usage_silent_text_emitted() -> None:
    assert adapter.parse(THOUGHT) == []
    assert adapter.parse(USAGE) == []
    events = adapter.parse(TEXT)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"


def test_parse_tool_call_and_update() -> None:
    call = adapter.parse(TOOL_CALL)
    assert call[0].type == "tool_call"
    assert call[0].data is not None
    assert call[0].data["tool"] == "read_file"
    assert adapter.parse(TOOL_UPDATE)[0].type == "step"


def test_parse_end_ok_captures_session_id() -> None:
    events = adapter.parse(END_OK)
    assert len(events) == 1
    assert events[0].type == "step"
    assert events[0].session_id == SESSION_ID


def test_parse_end_non_clean_is_error() -> None:
    events = adapter.parse(END_REFUSAL)
    assert events[0].type == "error"
    assert "refusal" in (events[0].text or "")


def test_parse_error_line() -> None:
    assert adapter.parse(ERROR_LINE)[0].type == "error"


def test_runhandle_captures_session_id_from_end() -> None:
    handle = RunHandle(
        proc=FakeProc(out=INIT + "\n" + END_OK + "\n"), parse=adapter.parse, name="grok"
    )
    events = list(handle.events())
    assert handle.session_id == SESSION_ID
    assert events[-1].type == "done"
