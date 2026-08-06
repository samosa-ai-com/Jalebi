import io

from jalebi.adapters.types import AgentEvent, RunHandle

TEXT_LINE = '{"type":"text","sessionID":"ses_1","part":{"type":"text","text":"hi"}}'


class FakeProc:
    def __init__(self, out: str = "", err: str = "", code: int = 0):
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)
        self._code = code
        self.returncode = None

    def wait(self) -> int:
        self.returncode = self._code
        return self._code


def _parse(line: str) -> list[AgentEvent]:
    return [AgentEvent(type="message", text=line, data={"session_id": "ses_1"})]


def test_events_stream_and_capture_session_id() -> None:
    proc = FakeProc(out=f"{TEXT_LINE}\n{len(TEXT_LINE) * 'x'}\n", err="", code=0)
    handle = RunHandle(proc=proc, parse=_parse)
    events = list(handle.events())
    assert [e.type for e in events] == ["message", "message", "done"]
    assert events[0].text == TEXT_LINE
    assert handle.session_id == "ses_1"


def test_done_on_exit_zero() -> None:
    handle = RunHandle(proc=FakeProc(out="", err="", code=0), parse=_parse)
    events = list(handle.events())
    assert events[-1].type == "done"


def test_error_on_nonzero_exit() -> None:
    handle = RunHandle(proc=FakeProc(out="", err="kaboom\n", code=3), parse=_parse)
    events = list(handle.events())
    assert events[-1].type == "error"
    assert events[-1].text is not None
    assert "3" in events[-1].text
    assert "kaboom" in events[-1].text


def test_stderr_tail_bounded() -> None:
    proc = FakeProc(out="", err="\n".join(f"line{i}" for i in range(200)) + "\n", code=0)
    handle = RunHandle(proc=proc, parse=_parse)
    list(handle.events())
    tail = handle.stderr_tail(5)
    assert tail.splitlines() == ["line195", "line196", "line197", "line198", "line199"]
