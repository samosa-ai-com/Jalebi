"""commandcode CLI adapter tests (PRD F4). Fixture lines mirror the live
``commandcode -p … --output-format json`` smoke run (commandcode 1.50.1,
Sep 2026)."""

import io

from jalebi.adapters.commandcode import CommandCodeAdapter
from jalebi.adapters.types import RunHandle

adapter = CommandCodeAdapter()

SESSION_ID = "5c1fe402-e4df-4810-8cfa-602edcd3bd8e"

RUN_START = (
    '{"type":"run_start","sessionId":"' + SESSION_ID + '",'
    '"model":"xiaomi/mimo-v2.5-pro"}'
)
TURN_START = '{"type":"turn_start","sessionId":"' + SESSION_ID + '","turnNumber":1}'
TEXT_DELTA = (
    '{"type":"text_delta","sessionId":"' + SESSION_ID + '","delta":"O"}'
)
THINKING_DELTA = (
    '{"type":"thinking_delta","sessionId":"' + SESSION_ID + '","delta":"user"}'
)
MESSAGE_END = (
    '{"type":"message_end","sessionId":"' + SESSION_ID + '",'
    '"message":{"role":"assistant","content":['
    '{"type":"thinking","text":"wants OK"},'
    '{"type":"text","text":"OK"}]}}'
)
TOOL_RUNNING = (
    '{"type":"tool_running","sessionId":"' + SESSION_ID + '",'
    '"toolCallId":"call_1","toolName":"read","description":"read x"}'
)
RUN_END = (
    '{"type":"run_end","sessionId":"' + SESSION_ID + '",'
    '"result":{"finalText":"OK","stopReason":"end_turn","turnCount":1}}'
)
RESULT_SUCCESS = (
    '{"type":"result","subtype":"success","sessionId":"' + SESSION_ID + '",'
    '"stopReason":"end_turn","finalText":"OK",'
    '"usage":{"inputTokens":16626,"outputTokens":21}}'
)
RESULT_EMPTY = (
    '{"type":"result","subtype":"success","sessionId":"' + SESSION_ID + '",'
    '"stopReason":"end_turn","finalText":""}'
)
RESULT_MAX_TURNS = (
    '{"type":"result","subtype":"max_turns","sessionId":"' + SESSION_ID + '",'
    '"finalText":""}'
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


def test_parse_run_start_captures_session_id() -> None:
    events = adapter.parse(RUN_START)
    assert len(events) == 1
    assert events[0].type == "step"
    assert events[0].session_id == SESSION_ID
    assert events[0].text is None


def test_parse_deltas_and_turn_markers_silent_or_step() -> None:
    assert adapter.parse(TEXT_DELTA) == []
    assert adapter.parse(THINKING_DELTA) == []
    assert adapter.parse(TURN_START)[0].type == "step"
    assert adapter.parse(RUN_END)[0].type == "step"


def test_parse_message_end_emits_text_not_thinking() -> None:
    events = adapter.parse(MESSAGE_END)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"


def test_parse_tool_running() -> None:
    events = adapter.parse(TOOL_RUNNING)
    assert events[0].type == "tool_call"
    assert events[0].data is not None
    assert events[0].data["tool"] == "read"


def test_parse_result_terminal() -> None:
    ok = adapter.parse(RESULT_SUCCESS)
    assert len(ok) == 1
    assert ok[0].type == "message"
    assert ok[0].text == "OK"
    assert adapter.parse(RESULT_EMPTY) == []
    err = adapter.parse(RESULT_MAX_TURNS)
    assert err[0].type == "error"


def test_runhandle_captures_session_id_from_run_start() -> None:
    handle = RunHandle(
        proc=FakeProc(out=RUN_START + "\n"), parse=adapter.parse, name="commandcode"
    )
    list(handle.events())
    assert handle.session_id == SESSION_ID
