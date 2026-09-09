"""qwen (Qwen Code) CLI adapter (PRD F4).

Maps ``qwen -p … -o stream-json --yolo`` (qwen 0.23.1, verified live Sep
2026 — see docs/03-adapters.md §10) events onto the normalized vocabulary.

Verified facts:
- JSONL, one object per line; every line carries top-level ``session_id``.
  Handshake is ``system``/``subtype:"init"`` (model, cwd, tools, permission
  mode). Terminal signal is the final ``result`` line (``subtype:"success"``
  + ``is_error:false``) and/or exit 0. Documented exit codes: 0 success,
  52 config, 53 turn-limit, 55 budget, 130 SIGINT.
- ``-p`` is deprecated but functional (positional query is the
  non-deprecated equivalent); ``-m`` overrides the configured model.
- ``--yolo`` prints a one-line stderr warning unless
  ``QWEN_CODE_SUPPRESS_YOLO_WARNING=1`` (set by the adapter).
- Live resume (``-r``/``-c`` follow-up), tool-call events, partial-message
  streaming and kill behavior were NOT live-exercised — mapped per
  docs/source, marked UNVERIFIED.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle


def _binary() -> str:
    binary = shutil.which("qwen")
    if binary is None:
        raise RuntimeError("qwen CLI not found on PATH")
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
    # Silence the headless --yolo stderr warning (verified live).
    full_env.setdefault("QWEN_CODE_SUPPRESS_YOLO_WARNING", "1")
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


def _join_text(content) -> str:
    """Flatten a ``tool_result.content`` value (list of ``{type:text}`` blocks or a raw str)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                text = b.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _content_events(content, *, user: bool = False) -> list[AgentEvent]:
    """Map a ``message.content[]`` array. ``thinking`` → silent."""
    if not isinstance(content, list):
        return []
    events: list[AgentEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if user:
            if btype == "tool_result":
                events.append(
                    AgentEvent(
                        type="tool_call",
                        data={
                            "tool": "tool_result",
                            "tool_use_id": block.get("tool_use_id"),
                            "output": _join_text(block.get("content")),
                        },
                    )
                )
            continue
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                events.append(AgentEvent(type="message", text=text))
        elif btype == "tool_use":
            events.append(
                AgentEvent(
                    type="tool_call",
                    data={
                        "tool": block.get("name"),
                        "input": block.get("input"),
                        "tool_use_id": block.get("id"),
                    },
                )
            )
        # thinking → silent
    return events


def _session_data(payload: dict) -> dict | None:
    sid = payload.get("session_id")
    return {"session_id": sid} if sid else None


def _settings_model_ids() -> list[str]:
    """Configured catalog: ``~/.qwen/settings.json`` → ``modelProviders`` ids.
    Reads ids only — never logs values (the file holds API keys)."""
    try:
        with open(Path.home() / ".qwen" / "settings.json", encoding="utf-8") as fh:
            settings = json.load(fh)
    except (OSError, ValueError):
        return []
    providers = settings.get("modelProviders") if isinstance(settings, dict) else None
    if not isinstance(providers, dict):
        return []
    ids: list[str] = []
    for entries in providers.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("id", "name", "model"):
                value = entry.get(key)
                if isinstance(value, str) and value and value not in ids:
                    ids.append(value)
                    break
    return ids


class QwenAdapter(AgentAdapter):
    id = "qwen"
    name = "qwen"

    def list_models(self) -> list[str]:
        return _settings_model_ids()

    def _base_args(self, model: str | None) -> list[str]:
        # No wall-clock cap: the queue's per-task timeout + stall watchdog own
        # the deadline (60 min default, escalated retries), so the agent never
        # dies early on a long task.
        args = [_binary(), "-o", "stream-json", "--yolo"]
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
        # Resume shape per docs/source; live follow-up UNVERIFIED (see module
        # docstring). Mirrors start argv + session pick.
        args = self._base_args(model) + ["-r", session_id, "-p", prompt]
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

        if event_type == "system":
            if payload.get("subtype") == "init":
                # Handshake carries the resume key; payload is never echoed.
                return [AgentEvent(type="step", phase="step", data=data)]
            return []
        if event_type == "stream_event":
            # goal_state snapshots + (with --include-partial-messages, which we
            # never pass) partial deltas. Silent either way.
            return []
        if event_type in ("assistant", "user"):
            message = payload.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            return _content_events(content, user=(event_type == "user"))
        if event_type == "result":
            subtype = str(payload.get("subtype") or "")
            is_error = payload.get("is_error") is True or subtype.startswith("error")
            if is_error:
                text = payload.get("error", {}).get("message") if isinstance(
                    payload.get("error"), dict
                ) else None
                return [AgentEvent(type="error", text=text or "qwen run failed", data=data)]
            final = payload.get("result")
            if isinstance(final, str) and final:
                return [AgentEvent(type="message", text=final, data=data)]
            return []
        if event_type in ("control_request", "control_response"):
            return []  # approval path; --yolo never emits it

        return [AgentEvent(type="message", text=line, data=data)]
