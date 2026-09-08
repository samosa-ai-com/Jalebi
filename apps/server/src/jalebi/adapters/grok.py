"""grok (Grok Build) CLI adapter (PRD F4).

Maps ``grok -p … --output-format streaming-json`` (grok 1.0.13, verified
live Sep 2026 — see docs/03-adapters.md §10) events onto the normalized
vocabulary. Strongest validation of the batch: start, ``-r`` resume,
``-c`` continue, SIGTERM-kill (143, session still resumable) and
``--always-approve`` were all exercised live.

Verified facts:
- NDJSON on stdout, diagnostics on stderr. First line is
  ``available_commands`` (tools/commands — the init signal).
- ``text`` chunks are concatenated by the consumer (stateless parser emits
  one message per chunk); ``thought`` chunks are silent.
- ``tool_call`` (in_progress) → tool_call; ``tool_call_update``
  (completed) → step. ``usage`` → silent.
- ``end`` is always last and carries ``sessionId`` — the ONLY in-stream
  resume key — so it maps to a ``step`` carrying the session (exit 0 then
  yields ``done`` via RunHandle). ``stopReason != end_turn`` maps to
  ``error``; ``error`` lines map to ``error`` (exit is non-zero).
- ``-s/--session-id`` creates a NEW session only (reusing an id errors,
  exit 1) — the adapter NEVER uses it for resume; resume is ``-r <id>``.
  Sessions are cwd-scoped (``--cwd`` must match on resume).
- ``--yolo`` is hidden but valid (``bypassPermissions``); documented alias
  ``--always-approve``. ``grok models`` is the model catalog (only
  ``grok-4.6`` at validation time). Exit codes: 0 ok, 1 error, 130 SIGINT,
  143 SIGTERM.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle

_MODEL_RE = re.compile(r"grok-[\w.]+")


def _binary() -> str:
    binary = shutil.which("grok")
    if binary is None:
        raise RuntimeError("grok CLI not found on PATH")
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
    sid = payload.get("sessionId")
    return {"session_id": sid} if sid else None


class GrokAdapter(AgentAdapter):
    id = "grok"
    name = "grok"

    def list_models(self) -> list[str]:
        # ``grok models`` is print-only; scrape model ids (verified shape).
        try:
            proc = subprocess.run([_binary(), "models"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        models: list[str] = []
        for token in _MODEL_RE.findall(proc.stdout):
            if token not in models:
                models.append(token)
        return models

    def _base_args(self, cwd: str, model: str | None) -> list[str]:
        args = [_binary(), "--output-format", "streaming-json", "--yolo", "--cwd", str(cwd)]
        if model:
            args += ["--model", model]
        return args

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        args = self._base_args(cwd, model) + ["-p", prompt]
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def resume(
        self,
        cwd: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        # ``-r`` resumes (verified live, same sessionId reused). NEVER
        # ``-s/--session-id`` here — it creates new-only and errors on reuse.
        args = self._base_args(cwd, model) + ["-p", prompt, "--resume", session_id]
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

        if event_type == "available_commands":
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type == "thought":
            return []  # reasoning deltas are silent (claude parity)
        if event_type == "text":
            chunk = payload.get("data")
            if isinstance(chunk, str) and chunk:
                return [AgentEvent(type="message", text=chunk, data=data)]
            return []
        if event_type == "tool_call":
            return [
                AgentEvent(
                    type="tool_call",
                    data={
                        "tool": payload.get("toolName"),
                        "tool_use_id": payload.get("toolCallId"),
                        "input": payload.get("rawInput"),
                        "session_id": (data or {}).get("session_id"),
                    },
                )
            ]
        if event_type == "tool_call_update":
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type == "usage":
            return []
        if event_type == "end":
            # Always last; carries the resume key. Non-clean stops → error,
            # clean stop → step (exit 0 yields done via RunHandle).
            if payload.get("stopReason", "end_turn") != "end_turn":
                return [
                    AgentEvent(
                        type="error",
                        text=f"grok run stopped: {payload.get('stopReason')}",
                        data=data,
                    )
                ]
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type == "error":
            text = payload.get("message") or "grok run failed"
            if not isinstance(text, str):
                text = "grok run failed"
            return [AgentEvent(type="error", text=text, data=data)]

        return [AgentEvent(type="message", text=line, data=data)]
