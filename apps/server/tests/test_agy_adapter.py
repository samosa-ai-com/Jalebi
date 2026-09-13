"""agy (Antigravity) CLI adapter tests (PRD F4). Fixture lines mirror live
``agy -p … --output-format stream-json`` runs (agy 1.2.2, Sep 2026)."""

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


def _capture_spawn(monkeypatch):
    """Replace agy._spawn with a recorder; returns the captured argv lists."""
    import jalebi.adapters.agy as agy_module

    captured: list[list[str]] = []
    monkeypatch.setattr(
        agy_module, "_spawn", lambda args, cwd, env=None: captured.append(args) or FakeProc()
    )
    monkeypatch.setattr(agy_module, "_binary", lambda: "agy")
    return captured


def test_argv_has_no_cli_print_timeout(monkeypatch) -> None:
    # The queue's per-task timeout + stall watchdog own the deadline — the
    # adapter must not impose its own (previously a fixed 10m print timeout).
    captured = _capture_spawn(monkeypatch)
    adapter.start("/tmp/ws/t1", "go")
    assert captured, "start must spawn"
    assert "--print-timeout" not in captured[0]
    adapter.resume("/tmp/ws/t1", CONV_ID, "go")
    assert "--print-timeout" not in captured[1]
    assert "--conversation" in captured[1] and CONV_ID in captured[1]


# --- 1.2.x shapes (live captures, Sep 2026) --------------------------------

TOOL_ACTIVE = (
    '{"event":"step_update","step_update":{"conversation_id":"' + CONV_ID + '",'
    '"step_index":4,"state":"ACTIVE","step_type":"tool",'
    '"tool_name":"write_to_file",'
    '"tool_info":{"name":"write_to_file",'
    '"parameters":{"TargetFile":"/tmp/agy-probe/hello.txt"}}}}'
)
TOOL_DONE = (
    '{"event":"step_update","step_update":{"conversation_id":"' + CONV_ID + '",'
    '"step_index":4,"state":"DONE","step_type":"tool",'
    '"tool_name":"write_to_file","duration_seconds":0.06,'
    '"tool_info":{"name":"write_to_file",'
    '"parameters":{"TargetFile":"/tmp/agy-probe/hello.txt"}}}}'
)
TOOL_DENIED = (
    '{"event":"step_update","step_update":{"conversation_id":"' + CONV_ID + '",'
    '"step_index":2,"state":"ERROR","step_type":"tool",'
    '"tool_name":"run_command","duration_seconds":0.06,'
    '"tool_info":{"name":"run_command","parameters":{"CommandLine":"pwd"},'
    '"error":{"type":"TOOL_ERROR",'
    '"message":"permission check failed for command \\"pwd\\""}}}}'
)
AGENT_USAGE_ONLY = (
    '{"event":"step_update","step_update":{"conversation_id":"' + CONV_ID + '",'
    '"step_index":1,"state":"DONE","step_type":"agent_response",'
    '"duration_seconds":0.02,'
    '"usage":{"input_tokens":17903,"output_tokens":129}}}'
)
RESULT_SUCCESS_RESPONSE = (
    '{"event":"result","result":{"conversation_id":"' + CONV_ID + '",'
    '"status":"SUCCESS","response":"DONE\\n","num_turns":1}}'
)
RESULT_SUCCESS_DENIED = (
    '{"event":"result","result":{"conversation_id":"' + CONV_ID + '",'
    '"status":"SUCCESS","response":"","num_turns":1,'
    '"denied_actions":[{"action":"command","display_name":"RunCommand"}]}}'
)


def test_parse_tool_active_is_liveness_step() -> None:
    events = adapter.parse(TOOL_ACTIVE)
    assert len(events) == 1
    assert events[0].type == "step"
    assert events[0].session_id == CONV_ID


def test_parse_tool_done_is_tool_call() -> None:
    events = adapter.parse(TOOL_DONE)
    assert len(events) == 1
    assert events[0].type == "tool_call"
    assert events[0].data is not None
    assert events[0].data["tool"] == "write_to_file"
    assert events[0].data["input"] == {"TargetFile": "/tmp/agy-probe/hello.txt"}


def test_parse_tool_error_stays_tool_call_not_error() -> None:
    # A single denied tool must not fail the whole run mid-stream — the
    # denial surfaces via the result's denied_actions message instead.
    events = adapter.parse(TOOL_DENIED)
    assert len(events) == 1
    assert events[0].type == "tool_call"
    assert events[0].data is not None
    assert "permission check failed" in str(events[0].data.get("output"))


def test_parse_usage_only_response_is_step() -> None:
    events = adapter.parse(AGENT_USAGE_ONLY)
    assert len(events) == 1
    assert events[0].type == "step"


def test_parse_success_denied_warns_visibly() -> None:
    events = adapter.parse(RESULT_SUCCESS_DENIED)
    assert len(events) == 1
    assert events[0].type == "message"
    assert "RunCommand" in (events[0].text or "")
    # The skip flag is always passed — never advise re-running with it.
    assert "already passed" in (events[0].text or "")
    assert "Re-run with" not in (events[0].text or "")


def test_run_parser_recovers_response_only_success() -> None:
    # Task-83 shape: textless turns, the result holds the only text.
    parse = adapter.run_parser()
    assert parse(INIT)[0].type == "step"
    assert parse(STEP_USER)[0].type == "step"
    assert parse(AGENT_USAGE_ONLY)[0].type == "step"
    assert parse(TOOL_DONE)[0].type == "tool_call"
    recovered = parse(RESULT_SUCCESS_RESPONSE)
    assert len(recovered) == 1
    assert recovered[0].type == "message"
    assert recovered[0].text == "DONE\n"


def test_run_parser_does_not_duplicate_streamed_text() -> None:
    # Normal shape: text streamed, then the same text in the result.
    parse = adapter.run_parser()
    assert parse(STEP_AGENT)[0].type == "message"
    assert parse(RESULT_SUCCESS_RESPONSE) == []


def test_run_parser_state_is_per_run() -> None:
    # Closure state must not leak across concurrent runs sharing the adapter.
    first = adapter.run_parser()
    second = adapter.run_parser()
    assert first(STEP_AGENT)[0].type == "message"
    # The second run streamed nothing: its response still surfaces.
    assert second(AGENT_USAGE_ONLY)[0].type == "step"
    recovered = second(RESULT_SUCCESS_RESPONSE)
    assert len(recovered) == 1 and recovered[0].text == "DONE\n"
