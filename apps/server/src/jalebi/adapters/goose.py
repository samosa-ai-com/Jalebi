"""goose CLI adapter (PRD F4).

Maps ``goose run -t … --output-format stream-json`` (goose 1.49.0, verified
live Sep 2026 — see docs/03-adapters.md §10) events onto the normalized
vocabulary.

Verified facts:
- Headless one-shot; default approval mode prompts nothing. Terminal event
  is ``complete`` (token/cost summary) + exit 0. CLI-level errors go to
  **stderr** with exit 1 (e.g. ``--session-id`` without ``--resume``).
- Stdout starts with a **non-JSON banner** (blank + 3 art/status lines)
  before the event payload — blank lines are dropped, other non-JSON lines
  surface verbatim per the house convention.
- Sessions are **named** (``-n``) and stored in a single global SQLite DB
  (``~/.local/share/goose/sessions/sessions.db``) recording the run cwd — a
  same-name resume from another worktree attaches to the same session, so the
  adapter derives a unique ``-n`` per worktree. Resume: ``goose run -r -n
  <name> -t …`` (``--session-id`` requires ``--resume``).
- ``--provider``/``--model`` override the configured defaults at run time;
  there is no list-models command — ``list_models`` returns ``[]``.
- Live resume and tool/diff event shapes were NOT live-exercised (trivial
  prompt only) — mapped defensively from the binary's event vocabulary
  (``tool_call``/``diff``/``error``/``done``/``step``/``progress``/
  ``session``/``user``/``comment``), marked UNVERIFIED.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle


def _binary() -> str:
    binary = shutil.which("goose")
    if binary is None:
        raise RuntimeError("goose CLI not found on PATH")
    return binary


def _session_name(cwd: str | Path) -> str:
    """Unique ``-n`` per worktree (global session DB is shared across cwds)."""
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(str(cwd)).name).strip("-") or "task"
    return f"jalebi-{base}"[:64]


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


def _text_of(block: dict) -> str | None:
    text = block.get("text")
    return text if isinstance(text, str) and text else None


def _message_events(message) -> list[AgentEvent]:
    """Map a ``message`` event's ``message.content[]``. ``thinking`` → silent;
    tool-shaped blocks → ``tool_call`` (shape UNVERIFIED live)."""
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    events: list[AgentEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "")
        if btype == "thinking":
            continue
        if "tool" in btype:
            events.append(
                AgentEvent(
                    type="tool_call",
                    data={
                        "tool": block.get("name") or block.get("tool") or btype,
                        "tool_use_id": block.get("id"),
                        "input": block.get("input"),
                        "output": block.get("output"),
                    },
                )
            )
        else:
            text = _text_of(block)
            if text:
                events.append(AgentEvent(type="message", text=text))
    return events


class GooseAdapter(AgentAdapter):
    id = "goose"
    name = "goose"

    def list_models(self) -> list[str]:
        # No list-models command/flag in goose — model selection is a
        # --provider/--model run-time override.
        return []

    def _base_args(
        self, cwd: str, prompt: str, model: str | None, provider: str | None = None
    ) -> list[str]:
        args = [
            _binary(),
            "run",
            "--output-format",
            "stream-json",
            "-n",
            _session_name(cwd),
            "-t",
            prompt,
        ]
        if provider:
            args += ["--provider", provider]
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
        return RunHandle(
            proc=_spawn(self._base_args(cwd, prompt, model), cwd, env),
            parse=self.parse,
            name=self.name,
        )

    def resume(
        self,
        cwd: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        # ``session_id`` is the ``-n`` name (recomputed identically). Resume
        # shape per docs; live resumed inference UNVERIFIED (see module
        # docstring). Mirrors start argv + resume flag.
        _ = session_id  # name is re-derived from cwd; kept for signature parity
        args = self._base_args(cwd, prompt, model)
        args[1:1] = ["-r"]
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def parse(self, line: str) -> list[AgentEvent]:
        if not line.strip():
            return []  # banner blank line
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return [AgentEvent(type="message", text=line)]
        if not isinstance(payload, dict):
            return [AgentEvent(type="message", text=line)]

        event_type = payload.get("type")
        if event_type == "message":
            events = _message_events(payload.get("message"))
            return events or []
        if event_type == "tool_call":
            raw_call = payload.get("tool_call")
            data = raw_call if isinstance(raw_call, dict) else {}
            return [
                AgentEvent(
                    type="tool_call",
                    data={
                        "tool": data.get("name") or data.get("tool"),
                        "input": data.get("input"),
                        "output": data.get("output"),
                    },
                )
            ]
        if event_type == "diff":
            raw_diff = payload.get("diff")
            diff_data = raw_diff if isinstance(raw_diff, dict) else {"patch": line}
            return [AgentEvent(type="diff", data=diff_data)]
        if event_type == "error":
            text = payload.get("message") or payload.get("error") or "goose run failed"
            if not isinstance(text, str):
                text = "goose run failed"
            return [AgentEvent(type="error", text=text)]
        if event_type in ("complete", "done"):
            return []  # terminal success → RunHandle yields done on exit 0
        if event_type in ("session", "step", "progress"):
            return [AgentEvent(type="step", phase="step")]
        if event_type in ("comment", "user"):
            text = payload.get("text") or payload.get("comment")
            if isinstance(text, str) and text:
                return [AgentEvent(type="message", text=text)]
            return []

        return [AgentEvent(type="message", text=line)]
