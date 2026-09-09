"""cline CLI adapter tests (PRD F4). Fixture lines mirror the live
``cline --json --yolo`` smoke run (cline 3.0.61, Sep 2026)."""

import io

from jalebi.adapters import cline as cline_module
from jalebi.adapters.cline import CLINE_CURATED, ClineAdapter, _harvest_bundle_models

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


def _write_bundle(path, chunks: list[bytes]) -> None:
    with open(path, "wb") as fh:
        for chunk in chunks:
            fh.write(chunk)


def test_harvest_picks_largest_dense_map(tmp_path, monkeypatch) -> None:
    bundle = tmp_path / ".cline"
    _write_bundle(
        bundle,
        [
            b'noise "z-ai/small":{id:"z-ai/small",name:"S"} tail',
            b"x" * 6000,
            b'"openai/big-a":{id:"openai/big-a"} mid "openai/big-b":{id:"openai/big-b"}',
        ],
    )
    assert _harvest_bundle_models(bundle) == ["openai/big-a", "openai/big-b"]


def test_harvest_missing_bundle_returns_empty(tmp_path) -> None:
    assert _harvest_bundle_models(tmp_path / ".cline") == []


def test_list_models_curated_first_then_harvested(tmp_path, monkeypatch) -> None:
    bundle = tmp_path / ".cline"
    _write_bundle(bundle, [b'"openai/big-a":{id:"openai/big-a"}'])
    monkeypatch.setattr(cline_module, "_bundle_path", lambda: bundle)
    cline_module._bundle_cache.clear()
    models = adapter.list_models()
    assert models[: len(CLINE_CURATED)] == CLINE_CURATED
    assert "openai/big-a" in models


def test_list_models_falls_back_to_curated_without_bundle(monkeypatch) -> None:
    monkeypatch.setattr(cline_module, "_bundle_path", lambda: None)
    assert adapter.list_models() == CLINE_CURATED


def _capture_spawn(monkeypatch):
    captured: list[list[str]] = []
    monkeypatch.setattr(
        cline_module,
        "_spawn",
        lambda args, cwd, env=None: captured.append(args) or FakeProc(),
    )
    monkeypatch.setattr(cline_module, "_binary", lambda: "cline")
    return captured


def test_argv_has_no_cli_timeout_cap(monkeypatch) -> None:
    # The queue's per-task timeout + stall watchdog own the deadline — the
    # adapter must not impose its own (previously a fixed 600s `--timeout`).
    captured = _capture_spawn(monkeypatch)
    adapter.start("/tmp/ws/t1", "go")
    assert captured, "start must spawn"
    assert "--timeout" not in captured[0]
    adapter.resume("/tmp/ws/t1", "ses_1", "go")
    assert "--timeout" not in captured[1]
    assert "--id" in captured[1] and "ses_1" in captured[1]


def test_resolve_session_reads_history(monkeypatch) -> None:
    class FakeRun:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode
            self.cwd: str | None = None

    calls: list[FakeRun] = []

    def fake_run(args, capture_output, text, timeout, cwd):
        proc = FakeRun('[{"id": "ses_hist_1", "ts": "2026-09-08"}]')
        proc.cwd = cwd
        calls.append(proc)
        return proc

    monkeypatch.setattr(cline_module.subprocess, "run", fake_run)
    assert adapter.resolve_session("/tmp/ws/t1") == "ses_hist_1"
    assert calls[0].cwd == "/tmp/ws/t1"


def test_resolve_session_handles_wrapped_and_bad_payloads(monkeypatch) -> None:
    class FakeRun:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    def run_with(stdout, returncode=0):
        return lambda *a, **k: FakeRun(stdout, returncode)

    monkeypatch.setattr(cline_module.subprocess, "run", run_with('{"sessions": [{"id": "s2"}]}'))
    assert adapter.resolve_session("/tmp/ws/t1") == "s2"
    monkeypatch.setattr(cline_module.subprocess, "run", run_with("not json"))
    assert adapter.resolve_session("/tmp/ws/t1") is None
    monkeypatch.setattr(cline_module.subprocess, "run", run_with("[]"))
    assert adapter.resolve_session("/tmp/ws/t1") is None
    monkeypatch.setattr(cline_module.subprocess, "run", run_with("{}", returncode=1))
    assert adapter.resolve_session("/tmp/ws/t1") is None
    monkeypatch.setattr(cline_module.subprocess, "run", run_with("[]", returncode=1))
    assert adapter.resolve_session("/tmp/ws/t1") is None
