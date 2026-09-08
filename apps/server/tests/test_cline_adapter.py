"""cline CLI adapter tests (PRD F4). Fixture lines mirror the live
``cline --json --yolo`` smoke run (cline 3.0.61, Sep 2026)."""

import io

from jalebi.adapters.cline import CLINE_CURATED, ClineAdapter

adapter = ClineAdapter()

ITERATION_START = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"agent_event",'
    '"event":{"type":"iteration_start","iteration":1}}'
)
CONTENT_START_TEXT = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"agent_event",'
    '"event":{"type":"content_start","contentType":"text","text":"OK","accumulated":"OK"}}'
)
CONTENT_END_TEXT = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"agent_event",'
    '"event":{"type":"content_end","contentType":"text","text":"OK"}}'
)
CONTENT_START_TOOL = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"agent_event",'
    '"event":{"type":"content_start","contentType":"tool","toolName":"read",'
    '"toolCallId":"call_1","input":{"path":"x"}}}'
)
CONTENT_END_TOOL = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"agent_event",'
    '"event":{"type":"content_end","contentType":"tool","output":"y","durationMs":5}}'
)
DONE = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"agent_event",'
    '"event":{"type":"done","reason":"completed","text":"Submission recorded","iterations":1}}'
)
AGENT_ERROR = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"agent_event",'
    '"event":{"type":"error","message":"invalid model format","recoverable":false}}'
)
RUN_RESULT_OK = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"run_result","finishReason":"completed",'
    '"iterations":1,"durationMs":34398,"model":{"id":"z-ai/glm-5.3-flash","provider":"cline"}}'
)
RUN_RESULT_ERROR = (
    '{"ts":"2026-09-08T19:07:13.303Z","type":"run_result","finishReason":"error",'
    '"text":"invalid model format. Expected format: modelType/model"}'
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


def test_parse_iteration_markers_are_steps() -> None:
    events = adapter.parse(ITERATION_START)
    assert len(events) == 1
    assert events[0].type == "step"


def test_parse_text_emitted_once_at_content_end() -> None:
    # content_start text duplicates content_end — only the end emits.
    assert adapter.parse(CONTENT_START_TEXT) == []
    events = adapter.parse(CONTENT_END_TEXT)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"


def test_parse_tool_start_maps_call_end_maps_step() -> None:
    start = adapter.parse(CONTENT_START_TOOL)
    assert start[0].type == "tool_call"
    assert start[0].data is not None
    assert start[0].data["tool"] == "read"
    assert start[0].data["input"] == {"path": "x"}
    assert adapter.parse(CONTENT_END_TOOL)[0].type == "step"


def test_parse_done_is_silent() -> None:
    # Terminal success → RunHandle yields done on exit 0.
    assert adapter.parse(DONE) == []


def test_parse_agent_error() -> None:
    events = adapter.parse(AGENT_ERROR)
    assert events[0].type == "error"
    assert "invalid model format" in (events[0].text or "")


def test_parse_run_result_terminal() -> None:
    assert adapter.parse(RUN_RESULT_OK) == []
    events = adapter.parse(RUN_RESULT_ERROR)
    assert events[0].type == "error"


def test_parse_non_json_verbatim() -> None:
    events = adapter.parse("banner")
    assert events[0].type == "message"


def test_curated_models_use_full_ids() -> None:
    assert "z-ai/glm-5.3-flash" in CLINE_CURATED
    assert all("/" in m for m in CLINE_CURATED)
