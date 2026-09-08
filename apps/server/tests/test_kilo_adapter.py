"""kilo (Kilo Code) CLI adapter tests (PRD F4). Fixture lines mirror the live
``kilo run --auto --format json`` smoke run (kilo 7.5.16, Sep 2026)."""

import io

from jalebi.adapters.kilo import KiloAdapter
from jalebi.adapters.types import RunHandle

adapter = KiloAdapter()

SESSION_ID = "ses_f7d976a78ffelea1nHoGixWt0e"

STEP_START = (
    '{"type":"step_start","timestamp":1788894000,"sessionID":"' + SESSION_ID + '",'
    '"part":{"type":"step-start"}}'
)
TEXT_LINE = (
    '{"type":"text","timestamp":1788894001,"sessionID":"' + SESSION_ID + '",'
    '"part":{"type":"text","text":"OK"}}'
)
STEP_FINISH_STOP = (
    '{"type":"step_finish","timestamp":1788894002,"sessionID":"' + SESSION_ID + '",'
    '"part":{"type":"step-finish","reason":"stop"}}'
)
STEP_FINISH_ABORT = (
    '{"type":"step_finish","timestamp":1788894002,"sessionID":"' + SESSION_ID + '",'
    '"part":{"type":"step-finish","reason":"aborted"}}'
)
ERROR_LINE = (
    '{"type":"error","sessionID":"' + SESSION_ID + '",'
    '"error":{"name":"UnknownError","data":{"message":"boom"}}}'
)
FILE_PATCH = (
    '{"type":"file","sessionID":"' + SESSION_ID + '",'
    '"part":{"type":"file","path":"a.py","patch":"@@ -1 +1 @@\\n-x\\n+y\\n"}}'
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


def test_parse_step_start_captures_session_id() -> None:
    events = adapter.parse(STEP_START)
    assert len(events) == 1
    assert events[0].type == "step"
    assert events[0].session_id == SESSION_ID


def test_parse_text_is_message() -> None:
    events = adapter.parse(TEXT_LINE)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"
    assert events[0].session_id == SESSION_ID


def test_parse_step_finish_stop_is_silent() -> None:
    # Terminal success → RunHandle yields done on exit 0.
    assert adapter.parse(STEP_FINISH_STOP) == []


def test_parse_step_finish_non_stop_is_error() -> None:
    events = adapter.parse(STEP_FINISH_ABORT)
    assert events[0].type == "error"
    assert "aborted" in (events[0].text or "")


def test_parse_error_line() -> None:
    events = adapter.parse(ERROR_LINE)
    assert events[0].type == "error"
    assert events[0].text == "boom"


def test_parse_file_patch_is_diff() -> None:
    events = adapter.parse(FILE_PATCH)
    assert events[0].type == "diff"
    assert events[0].data is not None
    assert events[0].data["path"] == "a.py"


def test_parse_non_json_verbatim() -> None:
    events = adapter.parse("not json")
    assert events[0].type == "message"
    assert events[0].text == "not json"


def test_runhandle_captures_session_id() -> None:
    handle = RunHandle(proc=FakeProc(out=STEP_START + "\n"), parse=adapter.parse, name="kilo")
    list(handle.events())
    assert handle.session_id == SESSION_ID
