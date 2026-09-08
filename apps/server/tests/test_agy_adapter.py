"""agy (Antigravity) CLI adapter tests (PRD F4). Fixture lines mirror the live
``agy -p … --output-format stream-json`` smoke run (agy 1.1.27, Sep 2026)."""

import io

from jalebi.adapters.agy import AGY_DEFAULT_MODEL, AgyAdapter
from jalebi.adapters.types import RunHandle

adapter = AgyAdapter()

CONV_ID = "f6e42baf-ef0c-4ecc-bb85-50cd7174a005"

INIT = (
    '{"event":"init","conversation_id":"' + CONV_ID + '",'
    '"init":{"cwd":"/tmp/opencode/agy-val","permission_mode":"always-proceed"}}'
)
STEP_USER = (
    '{"event":"step_update","step_update":{"conversation_id":"' + CONV_ID + '",'
    '"step_index":0,"state":"DONE","step_type":"user_input"}}'
)
STEP_AGENT = (
    '{"event":"step_update","step_update":{"conversation_id":"' + CONV_ID + '",'
    '"step_index":1,"state":"DONE","step_type":"agent_response",'
    '"text_delta":"OK\\n","duration_seconds":1.03}}'
)
STEP_TOOL = (
    '{"event":"step_update","step_update":{"conversation_id":"' + CONV_ID + '",'
    '"step_index":2,"state":"DONE","step_type":"tool_call",'
    '"tool_name":"read","input":{"path":"x"}}}'
)
RESULT_SUCCESS = (
    '{"event":"result","result":{"conversation_id":"' + CONV_ID + '",'
    '"status":"SUCCESS","response":"OK\\n","num_turns":1}}'
)
RESULT_ERROR = (
    '{"event":"result","result":{"conversation_id":"' + CONV_ID + '",'
    '"status":"ERROR","message":"kaput"}}'
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


def test_parse_init_captures_conversation_id() -> None:
    events = adapter.parse(INIT)
    assert len(events) == 1
    assert events[0].type == "step"
    assert events[0].session_id == CONV_ID
    assert events[0].text is None


def test_parse_step_updates() -> None:
    user = adapter.parse(STEP_USER)
    assert user[0].type == "step"
    assert user[0].session_id == CONV_ID
    agent = adapter.parse(STEP_AGENT)
    assert agent[0].type == "message"
    assert agent[0].text == "OK\n"
    tool = adapter.parse(STEP_TOOL)
    assert tool[0].type == "tool_call"
    assert tool[0].data is not None
    assert tool[0].data["tool"] == "read"


def test_parse_result_terminal() -> None:
    # SUCCESS text already streamed → silent; exit 0 yields done.
    assert adapter.parse(RESULT_SUCCESS) == []
    err = adapter.parse(RESULT_ERROR)
    assert err[0].type == "error"


def test_default_model_is_owner_choice() -> None:
    assert AGY_DEFAULT_MODEL == "gemini-3.8-flash-low"


def test_runhandle_captures_conversation_id_from_init() -> None:
    handle = RunHandle(proc=FakeProc(out=INIT + "\n"), parse=adapter.parse, name="agy")
    list(handle.events())
    assert handle.session_id == CONV_ID
