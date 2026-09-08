"""pi CLI adapter (PRD F4).

Maps ``pi --print --mode json`` (pi 0.80.2, verified live Sep 2026 — see
docs/03-adapters.md §10) events onto the normalized vocabulary.

Verified facts:
- JSONL, one object per line. The **resume key is the ``id`` of the first
  ``session`` line** (UUID); ``pi --session <id> "<msg>"`` / ``pi --continue
  "<msg>"`` resume headlessly. ``--resume`` opens an interactive TUI picker —
  never use it from the adapter.
- ``--model <pattern>`` accepts ``provider/id`` (e.g. ``openrouter/free``).
  **``openrouter/free`` is a pattern, not a fixed model** — it re-resolves per
  run and can hit an unavailable model.
- **Model errors keep exit code 0**: an unavailable model emits
  ``stopReason:"error"`` + ``errorMessage`` on the terminal events and still
  exits 0. The adapter therefore maps terminal ``stopReason:"error"`` to an
  ``error`` event instead of trusting the exit code alone.
- ``message_update`` lines carry streaming deltas (thinking/text/toolcall);
  the authoritative ``message_end`` carries the full content, so deltas are
  silent and only ``message_end`` emits. ``thinking`` blocks are silent.
- No dedicated diff event — diffs appear inside edit/write tool-call
  args/results. No ``done``-type event — ``agent_end`` + exit 0 is completion.
- Auth: ``~/.pi`` login + provider key env vars work headless; nothing to
  pass explicitly. ``--approve`` trusts project-local files (headless runs
  need it).
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle

# Default model pattern when a run pins none (the CLI default). A pattern, not
# a fixed model — see module docstring. Omitted from argv when model is None
# (CLI default applies); kept here for documentation/tests.
PI_DEFAULT_MODEL = "openrouter/free"


def _binary() -> str:
    binary = shutil.which("pi")
    if binary is None:
        raise RuntimeError("pi CLI not found on PATH")
    return binary


def _spawn(
    args: list[str], cwd: str | Path, env: dict[str, str | None] | None = None
) -> subprocess.Popen[str]:
    # Env built FROM the passed dict (None values dropped) — never
    # os.environ.copy()+overlay, so deliberately-removed keys cannot leak back.
    if env is None:
        full_env = os.environ.copy()
    else:
        full_env = {k: v for k, v in env.items() if v is not None}
    # Same shell wrapper as the other adapters (quirk parity).
    cmd = "cd " + shlex.quote(str(cwd)) + " && exec " + " ".join(shlex.quote(a) for a in args)
    return subprocess.Popen(
        ["/bin/bash", "-c", cmd],
        cwd=str(cwd),
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,  # own process group → cancel/timeout can killpg it
    )


def _session_data(payload: dict) -> dict | None:
    """Resume key: ``sessionId``/``session_id`` on event lines, ``id`` on the
    ``session`` header line only (other lines' ``id`` is a message id)."""
    if payload.get("type") == "session":
        sid = payload.get("id")
    else:
        sid = payload.get("sessionId") or payload.get("session_id")
    return {"session_id": sid} if sid else None


def _terminal_error(payload: dict) -> list[AgentEvent]:
    """``stopReason:"error"`` on a terminal event → ``error`` (exit code is 0
    even for model failures, so this cannot rely on RunHandle's exit check)."""
    message = payload.get("message") or {}
    stop = payload.get("stopReason")
    if stop is None and isinstance(message, dict):
        stop = message.get("stopReason")
    if stop != "error":
        return []
    text = payload.get("errorMessage")
    if not isinstance(text, str) or not text:
        text = message.get("errorMessage") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text:
        text = "pi run failed"
    return [AgentEvent(type="error", text=text, data=_session_data(payload))]


def _content_events(content) -> list[AgentEvent]:
    """Map a ``message_end.message.content[]`` array. ``thinking`` → silent."""
    if not isinstance(content, list):
        return []
    events: list[AgentEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                events.append(AgentEvent(type="message", text=text))
        elif btype in ("toolcall", "toolcall_start", "tool_call"):
            events.append(
                AgentEvent(
                    type="tool_call",
                    data={
                        "tool": block.get("toolName") or block.get("name"),
                        "tool_use_id": block.get("id"),
                        "input": block.get("input"),
                    },
                )
            )
        # thinking (+ unknown) → silent here; unknown full lines fall through
        # to the verbatim default in parse().
    return events


def _tool_name(payload: dict) -> str | None:
    for key in ("toolName", "tool", "name"):
        name = payload.get(key)
        if isinstance(name, str) and name:
            return name
    event = payload.get("assistantMessageEvent")
    if isinstance(event, dict):
        for key in ("toolName", "tool", "name"):
            name = event.get(key)
            if isinstance(name, str) and name:
                return name
    return None


class PiAdapter(AgentAdapter):
    id = "pi"
    name = "pi"

    def list_models(self) -> list[str]:
        # ``pi --list-models`` prints a table: provider, model, context, ...
        # (verified Sep 2026). Rebuild provider/model ids from columns 1-2.
        try:
            proc = subprocess.run(
                [_binary(), "--list-models"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        models: list[str] = []
        for line in proc.stdout.splitlines():
            cols = line.split()
            if len(cols) < 2 or cols[0] == "provider":
                continue
            models.append(f"{cols[0]}/{cols[1]}")
        return models

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        args = [_binary(), "--print", "--mode", "json", "--approve"]
        if model:
            args += ["--model", model]
        args.append(prompt)
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def resume(
        self,
        cwd: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        # ``--session`` (not ``--resume`` — that opens a TUI picker).
        args = [_binary(), "--print", "--mode", "json", "--approve", "--session", session_id]
        if model:
            args += ["--model", model]
        args.append(prompt)
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def parse(self, line: str) -> list[AgentEvent]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return [AgentEvent(type="message", text=line)]
        if not isinstance(payload, dict):
            return [AgentEvent(type="message", text=line)]

        event_type = payload.get("type")
        data = _session_data(payload)

        if event_type == "session":
            # Header carries the resume key; payload is never echoed.
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type in ("agent_start", "turn_start"):
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type in ("message_start", "message_update", "tool_execution_update"):
            return []  # streaming deltas; message_end is authoritative
        if event_type == "message_end":
            message = payload.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            events = _content_events(content)
            if events:
                return events
            return _terminal_error(payload)
        if event_type == "tool_execution_start":
            name = _tool_name(payload)
            if name:
                tool_data = {"tool": name}
                if data:
                    tool_data.update(data)
                return [AgentEvent(type="tool_call", data=tool_data)]
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type == "tool_execution_end":
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type in ("turn_end", "agent_end"):
            return _terminal_error(payload)
        if event_type == "error":
            text = payload.get("errorMessage") or payload.get("message") or "pi run failed"
            if not isinstance(text, str):
                text = "pi run failed"
            return [AgentEvent(type="error", text=text, data=data)]

        return [AgentEvent(type="message", text=line, data=data)]
