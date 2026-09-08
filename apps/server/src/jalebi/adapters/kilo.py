"""kilo (Kilo Code) CLI adapter (PRD F4).

Maps ``kilo run --auto --format json`` (kilo 7.5.16, verified live Sep 2026 —
see docs/03-adapters.md §10) events onto the normalized vocabulary. Kilo is an
OpenCode fork, so the event model mirrors opencode's: ``step_start`` → step,
``text`` → message, ``step_finish`` → terminal, ``error`` → error.

Verified facts:
- JSONL, one object per line; every line carries ``sessionID`` (the resume
  key). A bare model name (``Auto Free``) is rejected — ``-m`` needs the full
  ``provider/model`` id (e.g. ``kilo/kilo-auto/free``).
- ``kilo models`` lists ``provider/model`` ids, one per line.
- A successful run ends on ``step_finish`` (``reason:"stop"``) + exit 0; there
  is no separate ``done``-type event.
- Live resume (``-s``/``--continue`` with a follow-up prompt) and the
  ``tool_call``/``file``-patch event shapes were NOT live-exercised (trivial
  prompt only) — mapped defensively per docs/repo, marked UNVERIFIED.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle


def _binary() -> str:
    binary = shutil.which("kilo")
    if binary is None:
        raise RuntimeError("kilo CLI not found on PATH")
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


class KiloAdapter(AgentAdapter):
    id = "kilo"
    name = "kilo"

    def list_models(self) -> list[str]:
        # ``kilo models`` prints one provider/model id per line (verified).
        try:
            proc = subprocess.run([_binary(), "models"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        args = [_binary(), "run", "--auto", "--format", "json"]
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
        # Resume shape per --help/docs; live follow-up UNVERIFIED (see module
        # docstring). Mirrors start argv + session pick.
        args = [_binary(), "run", "--auto", "--format", "json", "--session", session_id]
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
        session_id = payload.get("sessionID")
        data = {"session_id": session_id} if session_id else None
        part = payload.get("part") or {}

        if event_type == "step_start":
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type == "text":
            text = part.get("text") if isinstance(part, dict) else None
            return [AgentEvent(type="message", text=text, data=data)]
        if event_type in ("tool_call_start", "tool_call"):
            # Shape UNVERIFIED live (no tool calls in the smoke run).
            tool_data = {"session_id": session_id} if session_id else {}
            if isinstance(part, dict):
                tool_data.update(
                    {
                        "tool": part.get("tool"),
                        "title": (part.get("state") or {}).get("title")
                        if isinstance(part.get("state"), dict)
                        else None,
                        "input": part.get("input"),
                        "output": part.get("output"),
                    }
                )
            return [AgentEvent(type="tool_call", data=tool_data)]
        if event_type == "file":
            # File-patch event (UNVERIFIED live) → normalized diff event.
            diff_data = {"session_id": session_id} if session_id else {}
            if isinstance(part, dict):
                diff_data.update({"path": part.get("path"), "patch": part.get("patch")})
            return [AgentEvent(type="diff", data=diff_data)]
        if event_type == "step_finish":
            reason = part.get("reason") if isinstance(part, dict) else None
            if reason is not None and reason != "stop":
                return [AgentEvent(type="error", text=f"kilo run finished: {reason}", data=data)]
            return []  # terminal success → RunHandle yields done on exit 0
        if event_type == "error":
            err = payload.get("error") or {}
            message = (err.get("data") or {}).get("message") or err.get("name") or "unknown error"
            return [AgentEvent(type="error", text=message, data=data)]

        return [AgentEvent(type="message", text=line, data=data)]
