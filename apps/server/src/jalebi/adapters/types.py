"""Agent adapter interface and normalized event vocabulary (PRD F4)."""

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

EVENT_TYPES = ("step", "tool_call", "message", "diff", "done", "error")

MAX_STDERR_LINES = 100


@dataclass
class AgentEvent:
    """One normalized agent event (PRD F4 vocabulary)."""

    type: str
    phase: str | None = None
    text: str | None = None
    data: dict[str, Any] | None = None

    @property
    def session_id(self) -> str | None:
        if self.data:
            return self.data.get("session_id")
        return None


@dataclass
class RunHandle:
    """A live agent run: child process + streaming parsed events."""

    proc: Any  # duck-typed: .stdout, .stderr, .wait()
    parse: Callable[[str], list[AgentEvent]]
    name: str = "opencode"  # adapter name, for exit-error text ("<name> exited with code N")
    session_id: str | None = field(default=None, init=False)
    _stderr_lines: list[str] = field(default_factory=list, init=False)
    _stderr_lock: Any = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        if self.proc.stderr is not None:
            threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            with self._stderr_lock:
                self._stderr_lines.append(line.rstrip("\n"))
                if len(self._stderr_lines) > MAX_STDERR_LINES:
                    del self._stderr_lines[0]

    def stderr_tail(self, n: int = 20) -> str:
        with self._stderr_lock:
            return "\n".join(self._stderr_lines[-n:])

    def events(self) -> Iterator[AgentEvent]:
        """Yield parsed events; ends with ``done``, or ``error`` (``{name} exited
        with code N`` + stderr tail) on non-zero exit."""
        assert self.proc.stdout is not None
        for raw_line in self.proc.stdout:
            line = raw_line.rstrip("\n")
            for event in self.parse(line):
                if event.session_id:
                    self.session_id = event.session_id
                yield event
        code = self.proc.wait()
        if code == 0:
            yield AgentEvent(type="done")
        else:
            tail = self.stderr_tail()
            yield AgentEvent(type="error", text=f"{self.name} exited with code {code}: {tail}")


class AgentAdapter:
    """Interface every CLI backend implements (start/resume/list_models/parse)."""

    id: str = ""
    name: str = ""

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        raise NotImplementedError

    def resume(
        self,
        cwd: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        raise NotImplementedError

    def parse(self, line: str) -> list[AgentEvent]:
        raise NotImplementedError
