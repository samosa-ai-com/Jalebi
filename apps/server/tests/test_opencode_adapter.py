import io

import pytest

from jalebi.adapters.opencode import OpenCodeAdapter

adapter = OpenCodeAdapter()

TEXT_LINE = (
    '{"type":"text","timestamp":1,"sessionID":"ses_1","part":'
    '{"id":"p","messageID":"m","sessionID":"ses_1","type":"text","text":"OK"}}'
)
TOOL_LINE = (
    '{"type":"tool_use","timestamp":1,"sessionID":"ses_1","part":'
    '{"type":"tool","tool":"bash","callID":"c","state":{"status":"completed",'
    '"input":{"command":"pwd"},"output":"/tmp\\n","title":"pwd"},"id":"p",'
    '"sessionID":"ses_1","messageID":"m"}}'
)
STEP_START_LINE = (
    '{"type":"step_start","timestamp":1,"sessionID":"ses_1","part":'
    '{"id":"p","messageID":"m","sessionID":"ses_1","type":"step-start"}}'
)
STEP_FINISH_LINE = (
    '{"type":"step_finish","timestamp":1,"sessionID":"ses_1","part":'
    '{"id":"p","reason":"stop","messageID":"m","sessionID":"ses_1","type":"step-finish"}}'
)
ERROR_LINE = (
    '{"type":"error","timestamp":1,"sessionID":"ses_1","error":'
    '{"name":"UnknownError","data":{"message":"boom","ref":"err_1"}}}'
)


def test_parse_text() -> None:
    events = adapter.parse(TEXT_LINE)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "OK"
    assert events[0].session_id == "ses_1"


def test_parse_tool_use() -> None:
    events = adapter.parse(TOOL_LINE)
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "tool_call"
    assert ev.data is not None
    assert ev.data["tool"] == "bash"
    assert ev.data["input"] == {"command": "pwd"}
    assert ev.data["output"] == "/tmp\n"


def test_parse_step_start() -> None:
    events = adapter.parse(STEP_START_LINE)
    assert len(events) == 1
    assert events[0].type == "step"


def test_parse_step_finish_is_silent() -> None:
    assert adapter.parse(STEP_FINISH_LINE) == []


def test_parse_error() -> None:
    events = adapter.parse(ERROR_LINE)
    assert len(events) == 1
    assert events[0].type == "error"
    assert events[0].text == "boom"


def test_parse_non_json_verbatim() -> None:
    events = adapter.parse("not json at all")
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == "not json at all"


def test_parse_unknown_json_verbatim() -> None:
    line = '{"type":"mystery","sessionID":"ses_1","data":1}'
    events = adapter.parse(line)
    assert len(events) == 1
    assert events[0].type == "message"
    assert events[0].text == line


def test_parse_non_dict_json_verbatim() -> None:
    events = adapter.parse("[1,2,3]")
    assert events[0].type == "message"


class FakeProc:
    def __init__(self, out: str = "", err: str = "", code: int = 0):
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)
        self._code = code
        self.returncode = None

    def wait(self) -> int:
        self.returncode = self._code
        return self._code


def test_start_command_construction(monkeypatch) -> None:
    captured: dict = {}

    def fake_spawn(args, cwd, env=None):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = env
        return FakeProc()

    monkeypatch.setattr("jalebi.adapters.opencode._spawn", fake_spawn)
    handle = adapter.start("/tmp/ws", "fix the bug", model="opencode-go/m1")
    args = captured["args"]
    assert args[0].endswith("opencode")
    assert "run" in args
    assert "--format" in args and args[args.index("--format") + 1] == "json"
    assert "--dir" in args and args[args.index("--dir") + 1] == "/tmp/ws"
    assert "--model" in args and args[args.index("--model") + 1] == "opencode-go/m1"
    assert args[-1] == "fix the bug"
    assert captured["cwd"] == "/tmp/ws"
    assert handle.proc is not None


def test_start_no_model(monkeypatch) -> None:
    captured: dict = {}

    def fake_spawn(args, cwd, env=None):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr("jalebi.adapters.opencode._spawn", fake_spawn)
    adapter.start("/tmp/ws", "hi")
    assert "--model" not in captured["args"]


def test_resume_command_construction(monkeypatch) -> None:
    captured: dict = {}

    def fake_spawn(args, cwd, env=None):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr("jalebi.adapters.opencode._spawn", fake_spawn)
    adapter.resume("/tmp/ws", "ses_abc", "keep going")
    args = captured["args"]
    assert "--session" in args and args[args.index("--session") + 1] == "ses_abc"
    assert args[-1] == "keep going"
    # resume must pass --dir matching the session's worktree, otherwise
    # opencode's headless --session resume emits nothing and hangs.
    assert args[args.index("--dir") + 1] == "/tmp/ws"


def test_list_models(monkeypatch) -> None:
    class FakeResult:
        returncode = 0
        stdout = "opencode/m1\nopencode/m2\n\n"

    monkeypatch.setattr("jalebi.adapters.opencode.subprocess.run", lambda *a, **k: FakeResult())
    assert adapter.list_models() == ["opencode/m1", "opencode/m2"]


def test_list_models_error_returns_empty(monkeypatch) -> None:
    class FakeResult:
        returncode = 1
        stdout = ""

    monkeypatch.setattr("jalebi.adapters.opencode.subprocess.run", lambda *a, **k: FakeResult())
    assert adapter.list_models() == []


def test_unknown_cli_rejected() -> None:
    from jalebi.adapters import get_adapter

    with pytest.raises(ValueError):
        get_adapter("codex")
    assert get_adapter("opencode").id == "opencode"
