"""commandcode (Command Code) CLI adapter (PRD F4).

Maps ``commandcode -p … --output-format json`` (commandcode 1.79.2 — see
docs/03-adapters.md §10) NDJSON events onto the normalized vocabulary.
Vendor: CommandCodeAI (docs: commandcode.ai/docs).

Verified facts:
- Turn-level events arrive wrapped in an envelope —
  ``{"type":"event","event":{…real event…}}`` — which the parser unwraps
  before mapping; ``run_start``/``run_end``/``result`` stay flat.
  (1.50.1 emitted everything flat; the envelope appeared by 1.53.1.)
- 18-line NDJSON run: ``run_start`` (carries ``sessionId``) → turn/message/
  model/thinking/text deltas → ``run_end`` → final ``result`` line
  (``subtype`` first: success/error/max_turns; ``finalText``/``usage``).
- ``-m`` accepts the full id (``xiaomi/mimo-v2.5-pro``) or the short name
  after the last ``/``. ``--list-models`` prints a sectioned table.
- stdout stays clean for piping; documented exit codes: 0 ok, 1 error,
  3 not-authed … 10 no-credits, 130 interrupted.
- ``--trust``/``--skip-onboarding``/``--no-auto-update`` verified in the
  live run. Live resume (``--resume``/``--continue``), signal-kill and
  ``tool_running`` frames were NOT live-exercised — mapped per docs, marked
  UNVERIFIED. Per docs ``--yolo`` is needed for file-write/shell tools in
  headless (default blocks them) — also UNVERIFIED, so the adapter stays on
  the verified argv and notes the caveat.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle


def _binary() -> str:
    binary = shutil.which("commandcode")
    if binary is None:
        raise RuntimeError("commandcode CLI not found on PATH")
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


def _content_events(content, data: dict | None = None) -> list[AgentEvent]:
    """Map a ``message_end.content[]`` array. ``thinking`` → silent.

    ``data`` (resume key) rides on every emitted event so turns never lose
    their session, whether it came from the flat line or the envelope.
    """
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
                events.append(AgentEvent(type="message", text=text, data=data))
        elif btype in ("tool_use", "tool_running"):
            events.append(
                AgentEvent(
                    type="tool_call",
                    data={
                        **(data or {}),
                        "tool": block.get("toolName") or block.get("name"),
                        "tool_use_id": block.get("toolCallId") or block.get("id"),
                        "input": block.get("input"),
                        "description": block.get("description"),
                    },
                )
            )
        # thinking → silent
    return events


class CommandCodeAdapter(AgentAdapter):
    id = "commandcode"
    name = "commandcode"

    def list_models(self) -> list[str]:
        # Sectioned table — first column holds ids (full or short); skip
        # headers/section titles (tokens without a "/").
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
            if not cols or "/" not in cols[0]:
                continue
            if cols[0] not in models:
                models.append(cols[0])
        return models

    def _base_args(self, model: str | None) -> list[str]:
        args = [
            _binary(),
            "--output-format",
            "json",
            "--skip-onboarding",
            "--no-auto-update",
            "--trust",
        ]
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
        # Resume shape per docs; live follow-up UNVERIFIED (see module
        # docstring). Mirrors start argv + session pick.
        args = self._base_args(model) + ["-p", "--resume", session_id, prompt]
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def parse(self, line: str) -> list[AgentEvent]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return [AgentEvent(type="message", text=line)]
        if not isinstance(payload, dict):
            return [AgentEvent(type="message", text=line)]

        # 1.53.x wraps turn-level events: {"type":"event","event":{…}}.
        # Unwrap so the mapping below sees the real event; anything else
        # keeps the old verbatim fallback. The resume key is read from the
        # inner event first, then the envelope (currently always inner —
        # the outer read is belt-and-braces for future shapes).
        outer = payload
        if payload.get("type") == "event" and isinstance(payload.get("event"), dict):
            payload = payload["event"]

        event_type = payload.get("type")
        data = _session_data(payload) or _session_data(outer)

        if event_type == "run_start":
            # Header carries the resume key; payload is never echoed.
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type in ("turn_start", "turn_end", "run_end"):
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type in (
            "message_start",
            "message_update",
            "text_delta",
            "thinking_start",
            "thinking_delta",
            "thinking_end",
            "model_request_start",
            "model_request_end",
        ):
            return []  # deltas/noise; message_end is authoritative
        if event_type == "message_end":
            message = payload.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            return _content_events(content, data)
        if event_type == "tool_running":
            return _content_events(
                [
                    {
                        "type": "tool_running",
                        "toolName": payload.get("toolName"),
                        "toolCallId": payload.get("toolCallId"),
                        "input": payload.get("input"),
                        "description": payload.get("description"),
                    }
                ],
                data,
            )
        if event_type in ("tool_completed", "tool_errored"):
            # Terminal tool output (1.79.x): the result/error of the call.
            tool_data = {
                **(data or {}),
                "tool": payload.get("toolName") or payload.get("name"),
                "tool_use_id": payload.get("toolCallId") or payload.get("id"),
                "input": payload.get("input"),
                "status": "error" if event_type == "tool_errored" else "completed",
            }
            if event_type == "tool_completed":
                tool_data["output"] = payload.get("result") or payload.get("output")
            else:
                err = payload.get("error")
                tool_data["error"] = err if isinstance(err, str) else (err or "tool failed")
            return [AgentEvent(type="tool_call", data=tool_data)]
        if event_type in ("tool_denied", "tool_hook_blocked"):
            # Terminal tool outcomes (like tool_completed/tool_errored):
            # surface the denial visibly so headless permission blocks are
            # distinguishable from lost output (also the evidence that settles
            # the --trust vs --yolo question).
            reason = payload.get("denyMessage") or payload.get("deny_message")
            if not isinstance(reason, str) or not reason:
                reason = payload.get("reason") or payload.get("error") or payload.get("message")
            if not isinstance(reason, str) or not reason:
                reason = "tool denied"
            return [
                AgentEvent(
                    type="tool_call",
                    data={
                        **(data or {}),
                        "tool": payload.get("toolName") or payload.get("name"),
                        "tool_use_id": payload.get("toolCallId") or payload.get("id"),
                        "input": payload.get("input"),
                        "status": "denied",
                        "error": reason,
                    },
                )
            ]
        if event_type == "run_error":
            # Genuine failure signal (the CLI throws on it in some paths).
            err = payload.get("error")
            if isinstance(err, dict):
                text = err.get("message")
            elif isinstance(err, str):
                text = err
            else:
                text = None
            if not isinstance(text, str) or not text:
                text = "commandcode run failed"
            return [AgentEvent(type="error", text=text, data=data)]
        if event_type == "interrupted":
            text = payload.get("message")
            if not isinstance(text, str) or not text:
                text = "commandcode run interrupted"
            return [AgentEvent(type="message", text=text, data=data)]
        if event_type == "mod_error":
            # Extension hook failure — non-terminal by construction (the run
            # proceeds), so a visible notice, never an error event.
            mod = payload.get("modId") or payload.get("mod")
            err = payload.get("error")
            text = "commandcode mod error" + (f" in {mod}" if isinstance(mod, str) and mod else "")
            if isinstance(err, str) and err:
                text += f": {err}"
            return [AgentEvent(type="message", text=text, data=data)]
        if event_type in (
            # 1.79.x lifecycle noise: no timeline content.
            "tool_queued",
            "tool_update",
            "tool_hooks",
            "tool_input_coerced",
            "tool_input_repaired",
            "notice",
            "skill_loaded",
            "session_titled",
            "permission_mode_changed",
            "config_setting_changed",
            "api_retry",
            "continuation_recovery",
            "subagent_start",
            "subagent_stop",
            "subagent_progress",
            "compaction_start",
            "compaction_done",
        ):
            return []
        if event_type == "result":
            if payload.get("subtype") != "success":
                text = payload.get("error") or payload.get("finalText") or "commandcode run failed"
                if not isinstance(text, str):
                    text = "commandcode run failed"
                return [AgentEvent(type="error", text=text, data=data)]
            final = payload.get("finalText")
            if isinstance(final, str) and final:
                return [AgentEvent(type="message", text=final, data=data)]
            return []
        if event_type == "error":
            text = payload.get("error") or payload.get("message") or "commandcode run failed"
            if not isinstance(text, str):
                text = "commandcode run failed"
            return [AgentEvent(type="error", text=text, data=data)]

        return [AgentEvent(type="message", text=line, data=data)]
