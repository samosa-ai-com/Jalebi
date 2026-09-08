"""agy (Antigravity) CLI adapter (PRD F4).

Maps ``agy -p … --output-format stream-json`` (agy 1.1.27, verified live
Sep 2026 — see docs/03-adapters.md §10) events onto the normalized
vocabulary. Official Google product; auth is the owner's OAuth login
(reused non-interactively — verified live).

Verified facts:
- 4-event stream: ``init`` (``conversation_id`` = resume key, tools,
  permission mode) → ``step_update`` (``user_input``/``agent_response`` with
  ``text_delta``) → ``result`` (``status:"SUCCESS"``, ``response``).
  Exit 0, empty stderr.
- The known ``--print``-drops-stdout-on-non-TTY issue does NOT reproduce on
  1.1.27 (full stream arrived via pipe).
- ``--conversation <id>`` / ``--continue`` resume; ``--model`` selects the
  model; ``--dangerously-skip-permissions`` +
  ``--print-timeout`` bound headless runs. ``agy models`` lists ids.
- Default model is the owner-chosen ``gemini-3.8-flash-low`` (passed
  explicitly so runs never drift with the CLI default).
- Live resume (``--conversation`` follow-up), kill-during-model-run and
  non-default ``--model`` execution were NOT live-exercised — per
  flags/docs, marked UNVERIFIED.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle

# Owner-chosen default (verified listed by `agy models`); always passed
# explicitly so runs never drift with the CLI default.
AGY_DEFAULT_MODEL = "gemini-3.8-flash-low"


def _binary() -> str:
    binary = shutil.which("agy")
    if binary is None:
        raise RuntimeError("agy CLI not found on PATH")
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


def _conversation_data(payload: dict) -> dict | None:
    """Resume key: top-level ``conversation_id``, or nested under the
    ``step_update``/``result`` envelope."""
    sid = payload.get("conversation_id")
    if sid is None:
        for key in ("step_update", "result"):
            envelope = payload.get(key)
            if isinstance(envelope, dict):
                sid = envelope.get("conversation_id")
                if sid:
                    break
    return {"session_id": sid} if sid else None


class AgyAdapter(AgentAdapter):
    id = "agy"
    name = "agy"

    def list_models(self) -> list[str]:
        # ``agy models`` prints id + display name per line after a
        # "Fetching available models..." progress line (verified shape).
        try:
            proc = subprocess.run([_binary(), "models"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        models: list[str] = []
        for line in proc.stdout.splitlines():
            cols = line.split()
            if len(cols) < 2 or cols[0].lower().startswith("fetching"):
                continue
            if cols[0] not in models:
                models.append(cols[0])
        return models

    def _base_args(self, model: str | None) -> list[str]:
        return [
            _binary(),
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "--print-timeout",
            "10m",
            "--model",
            model or AGY_DEFAULT_MODEL,
        ]

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        args = self._base_args(model) + ["-p", prompt]
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def resume(
        self,
        cwd: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        # Resume shape per --help; live follow-up UNVERIFIED (see module
        # docstring). Mirrors start argv + conversation pick.
        args = self._base_args(model) + ["--conversation", session_id, "-p", prompt]
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def parse(self, line: str) -> list[AgentEvent]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return [AgentEvent(type="message", text=line)]
        if not isinstance(payload, dict):
            return [AgentEvent(type="message", text=line)]

        event = payload.get("event")
        data = _conversation_data(payload)

        if event == "init":
            # Header carries the resume key; payload is never echoed.
            return [AgentEvent(type="step", phase="step", data=data)]
        if event == "step_update":
            update = payload.get("step_update") or {}
            step_type = update.get("step_type") if isinstance(update, dict) else None
            if step_type == "tool_call":
                tool_data = {"session_id": (data or {}).get("session_id")}
                if isinstance(update, dict):
                    tool_data.update(
                        {
                            "tool": update.get("tool_name") or update.get("tool"),
                            "input": update.get("input"),
                            "output": update.get("output"),
                        }
                    )
                return [AgentEvent(type="tool_call", data=tool_data)]
            if step_type == "agent_response":
                text = update.get("text_delta") if isinstance(update, dict) else None
                if isinstance(text, str) and text:
                    return [AgentEvent(type="message", text=text, data=data)]
                return [AgentEvent(type="step", phase="step", data=data)]
            return [AgentEvent(type="step", phase="step", data=data)]
        if event == "result":
            result = payload.get("result") or {}
            status = result.get("status") if isinstance(result, dict) else None
            if status == "SUCCESS":
                return []  # text already streamed; exit 0 yields done
            text = result.get("message") if isinstance(result, dict) else None
            if not isinstance(text, str) or not text:
                text = f"agy run failed: {status}"
            return [AgentEvent(type="error", text=text, data=data)]

        return [AgentEvent(type="message", text=line, data=data)]
