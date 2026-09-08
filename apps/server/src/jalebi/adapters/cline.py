"""cline CLI adapter (PRD F4).

Maps ``cline --json --yolo`` (cline 3.0.61, verified live Sep 2026 — see
docs/03-adapters.md §10) NDJSON events onto the normalized vocabulary. Note
the wire schema is ``agent_event``/``run_result`` — the older ``say``/``ask``
schema still in some docs is stale.

Verified facts:
- One-shot headless run; ``--yolo`` is a hidden alias of
  ``--auto-approve true`` (works; verified in the live run).
  ``-t/--timeout`` is in **seconds**. ``-c/--cwd`` sets the workdir.
- ``-m`` needs the full ``modelType/model`` id (e.g. ``z-ai/glm-5.3-flash``;
  the display name ``GLM-5.3-Flash`` is rejected with exit 1). No
  ``--list-models``/``models`` subcommand exists — the adapter returns a
  curated free-tier list (ids verified present in the installed bundle),
  same precedent as ``CLAUDE_CURATED``.
- Terminal line is ``run_result`` (``finishReason:"completed"`` + exit 0, or
  ``"error"`` + exit 1). `done` events carry reason/text/iterations.
- **Session id is NOT in the stdout stream** — the resume key lives in
  ``cline history --json`` (latest entry). The adapter therefore cannot
  capture it during streaming; follow-ups should resolve the id from history
  (future improvement), else they fall back to a fresh session.
- Live resume (``--id`` + prompt) and diff shapes were NOT live-exercised —
  mapped per docs, marked UNVERIFIED.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle

# Curated free-tier ids — verified present in the installed 3.0.61 bundle.
# Overridable via the `adapter_model_lists` setting (same precedent as claude).
CLINE_CURATED = [
    "z-ai/glm-5.3-flash",
    "z-ai/glm-5.3-free",
    "z-ai/glm-5.2-free",
    "z-ai/glm-4.7-flash-free",
]


def _binary() -> str:
    binary = shutil.which("cline")
    if binary is None:
        raise RuntimeError("cline CLI not found on PATH")
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


def _agent_event(event: dict) -> list[AgentEvent]:
    """Map the inner ``event`` object of an ``agent_event`` line."""
    etype = event.get("type")
    if etype in ("iteration_start", "iteration_end"):
        return [AgentEvent(type="step", phase="step")]
    if etype == "content_start":
        if event.get("contentType") == "tool":
            return [
                AgentEvent(
                    type="tool_call",
                    data={
                        "tool": event.get("toolName"),
                        "tool_use_id": event.get("toolCallId"),
                        "input": event.get("input"),
                    },
                )
            ]
        return []  # text content_start duplicates content_end; usage is noise
    if etype == "content_end":
        if event.get("contentType") == "tool":
            return [AgentEvent(type="step", phase="step")]
        text = event.get("text")
        if isinstance(text, str) and text:
            return [AgentEvent(type="message", text=text)]
        return []
    if etype == "done":
        return []  # terminal success → RunHandle yields done on exit 0
    if etype == "error":
        return [AgentEvent(type="error", text=event.get("message") or "cline run failed")]
    if etype == "usage":
        return []
    return [AgentEvent(type="message", text=json.dumps(event))]


class ClineAdapter(AgentAdapter):
    id = "cline"
    name = "cline"

    def list_models(self) -> list[str]:
        # No list command — curated ids (verified in-bundle), same precedent
        # as claude's CLAUDE_CURATED.
        return list(CLINE_CURATED)

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        args = [_binary(), "--json", "--yolo", "--cwd", str(cwd), "--timeout", "600"]
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
        args = [_binary(), "--json", "--yolo", "--cwd", str(cwd), "--timeout", "600",
                "--id", session_id]
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

        line_type = payload.get("type")
        if line_type == "agent_event":
            event = payload.get("event")
            if isinstance(event, dict):
                return _agent_event(event)
            return [AgentEvent(type="message", text=line)]
        if line_type == "run_result":
            if payload.get("finishReason") == "error":
                return [AgentEvent(type="error", text=payload.get("text") or "cline run failed")]
            return []  # completed → RunHandle yields done on exit 0

        return [AgentEvent(type="message", text=line)]
