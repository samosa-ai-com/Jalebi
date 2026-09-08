"""goose CLI adapter tests (PRD F4). Fixture lines mirror the live
``goose run -t … --output-format stream-json`` smoke run (goose 1.49.0,
Sep 2026)."""

import io

from jalebi.adapters.goose import GooseAdapter, _session_name
from jalebi.adapters.types import RunHandle

adapter = GooseAdapter()

BANNER_BLANK = ""
BANNER_LINE = "     L L     goose is ready"
MESSAGE_THINKING = (
    '{"type":"message","message":{"id":"gen-1","role":"assistant",'
    '"content":[{"type":"thinking","thinking":"The"}]}}'
)
MESSAGE_TEXT = (
    '{"type":"message","message":{"id":"gen-1","role":"assistant",'
    '"content":[{"type":"thinking","thinking":"The"},'
    '{"type":"text","text":"OK"}]}}'
)
COMPLETE = (
    '{"type":"complete","total_tokens":9272,"input_tokens":9244,'
    '"output_tokens":28,"cost_usd":0.0}'
)
TOOL_CALL = (
    '{"type":"tool_call","tool_call":{"name":"read","input":{"path":"x"}}}'
)
ERROR_LINE = '{"type":"error","message":"kaput"}'
STEP_LINE = '{"type":"step","step":"working"}'


class FakeProc:
    def __init__(self, out: str = "", err: str = "", code: int = 0):
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)
        self._code = code

    def poll(self):
        return None

    def wait(self) -> int:
        return self._code


def test_session_name_unique_per_worktree() -> None:
    assert _session_name("/tmp/ws/task-12") == "jalebi-task-12"
    assert _session_name("/tmp/ws/task-12") != _session_name("/tmp/ws/task-13")
    assert _session_name("/tmp/ws/weird name!") == "jalebi-weird-name"


def test_parse_banner_blank_dropped_others_verbatim() -> None:
    assert adapter.parse(BANNER_BLANK) == []
    events = adapter.parse(BANNER_LINE)
    assert events[0].type == "message"


def test_parse_thinking_silent_text_emitted() -> None:
    assert adapter.parse(MESSAGE_THINKING) == []
    events = adapter.parse(MESSAGE_TEXT)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"


def test_parse_complete_is_silent() -> None:
    # Terminal success → RunHandle yields done on exit 0.
    assert adapter.parse(COMPLETE) == []


def test_parse_tool_call_and_error() -> None:
    tool = adapter.parse(TOOL_CALL)
    assert tool[0].type == "tool_call"
    assert tool[0].data is not None
    assert tool[0].data["tool"] == "read"
    err = adapter.parse(ERROR_LINE)
    assert err[0].type == "error"


def test_parse_step_markers() -> None:
    assert adapter.parse(STEP_LINE)[0].type == "step"


def test_runhandle_completes_on_exit_zero() -> None:
    handle = RunHandle(
        proc=FakeProc(out=MESSAGE_TEXT + "\n" + COMPLETE + "\n"),
        parse=adapter.parse,
        name="goose",
    )
    events = list(handle.events())
    assert events[-1].type == "done"
