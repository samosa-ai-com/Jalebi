"""agy (Antigravity) CLI adapter (PRD F4).

Maps ``agy -p … --output-format stream-json`` (agy 1.2.2, verified live
Sep 2026 — see docs/03-adapters.md §10) events onto the normalized
vocabulary. Official Google product; auth is the owner's OAuth login
(reused non-interactively — verified live).

Verified facts:
- 4-event stream: ``init`` (``conversation_id`` = resume key, tools,
  ``permission_mode``) → ``step_update`` (``user_input``/``agent_response``
  with ``text_delta``/``tool`` with nested ``tool_info``) → ``result``
  (``status:"SUCCESS"``, ``response``, optional ``denied_actions``).
  Exit 0, empty stderr.
- Tool calls arrive as ``step_type:"tool"`` (``ACTIVE``/``DONE``/``ERROR``,
  ``tool_info.parameters``/``output``/``error``) — the older flat
  ``"tool_call"`` shape is still accepted defensively.
- ``agent_response`` turns without ``text_delta`` (usage-only ``DONE``) are
  normal mid-run; the ``result.response`` may be the ONLY text, so the
  per-run parser (``run_parser``) recovers it instead of dropping it.
- Headless denials (no ``--dangerously-skip-permissions``) auto-deny the
  tool: the ``tool`` turn goes ``ERROR`` (``tool_info.error.message``),
  ``result`` stays ``SUCCESS`` with ``denied_actions`` and exit 0, plus a
  ``jetski:`` notice on stderr.
- The known ``--print``-drops-stdout-on-non-TTY issue does NOT reproduce on
  1.2.2 (full stream arrived via pipe).
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
    ``step_update``/``result`` envelope. The ``init`` permission mode
    (``always-proceed`` vs ``request-review``) rides along for diagnostics."""
    sid = payload.get("conversation_id")
    if sid is None:
        for key in ("step_update", "result"):
            envelope = payload.get(key)
            if isinstance(envelope, dict):
                sid = envelope.get("conversation_id")
                if sid:
                    break
    if not sid:
        return None
    data: dict = {"session_id": sid}
    init = payload.get("init")
    if isinstance(init, dict):
        mode = init.get("permission_mode")
        if isinstance(mode, str) and mode:
            data["permission_mode"] = mode
    return data


def _tool_info_parts(info: object) -> tuple[object, object]:
    """``(input, output)`` from a 1.2.x nested ``tool_info`` envelope.

    ``parameters`` become the input; ``output`` is the output, with a
    ``TOOL_ERROR`` message appended when the turn errored (e.g. a headless
    permission denial).
    """
    if not isinstance(info, dict):
        return None, None
    output = info.get("output")
    err = info.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg:
            output = (str(output) + "\n" if output else "") + "error: " + msg
    return info.get("parameters"), output


def _denied_names(result: dict) -> list[str]:
    """Human-readable names from a ``result.denied_actions`` list."""
    denied = result.get("denied_actions")
    if not isinstance(denied, list):
        return []
    names: list[str] = []
    for item in denied:
        if not isinstance(item, dict):
            continue
        name = item.get("display_name") or item.get("action")
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


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
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.run_parser(), name=self.name)

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
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.run_parser(), name=self.name)

    def run_parser(self):
        """Per-run line parser: ``parse`` plus response recovery.

        ``parse`` is stateless, so a ``result.response`` that arrives with
        ``status:"SUCCESS"`` is dropped there (text is assumed streamed).
        On 1.2.x the response is sometimes the ONLY text (textless
        ``agent_response`` turns), which produced false ``empty_done``
        failures. This closure tracks whether any message text streamed and
        recovers exactly that case as one message. The state lives in the
        closure — one per ``RunHandle`` — so concurrent runs never share it.
        """
        seen_text = False

        def parse_line(line: str) -> list[AgentEvent]:
            nonlocal seen_text
            events = self.parse(line)
            for event in events:
                if event.type == "message" and event.text:
                    seen_text = True
            if not events and not seen_text:
                # Only a SUCCESS result parses to [] — recover its response
                # when nothing streamed (denials already surfaced as messages
                # by parse, so reaching here means a bare silent success).
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    return events
                if isinstance(payload, dict) and payload.get("event") == "result":
                    result = payload.get("result")
                    if isinstance(result, dict) and result.get("status") == "SUCCESS":
                        response = result.get("response")
                        if isinstance(response, str) and response.strip():
                            seen_text = True
                            return [
                                AgentEvent(
                                    type="message",
                                    text=response,
                                    data=_conversation_data(payload),
                                )
                            ]
            return events

        return parse_line

    def parse(self, line: str) -> list[AgentEvent]:
        """Stateless line parser (interface + unit-test entry point).

        ``result``/``SUCCESS`` responses are dropped here — text is assumed
        already streamed. Runs use ``run_parser()``, which recovers the
        response when it turns out to be the only text.
        """
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
                # 1.1.x flat shape (kept defensively).
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
            if step_type == "tool":
                # 1.2.x shape: {"tool_name", "state": ACTIVE/DONE/ERROR,
                # "tool_info": {"parameters", "output", "error"}}.
                state = update.get("state") if isinstance(update, dict) else None
                if state == "ACTIVE":
                    # Liveness only — the DONE/ERROR turn carries the result.
                    return [AgentEvent(type="step", phase="step", data=data)]
                params, output = _tool_info_parts(
                    update.get("tool_info") if isinstance(update, dict) else None
                )
                tool_data = {"session_id": (data or {}).get("session_id")}
                if isinstance(update, dict):
                    tool_data.update(
                        {
                            "tool": update.get("tool_name"),
                            "state": state,
                            "input": params,
                            "output": output,
                        }
                    )
                # ERROR stays a tool_call (a single denied tool must not fail
                # the whole run mid-stream — the denial surfaces via the
                # result's denied_actions message instead).
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
            denied = _denied_names(result) if isinstance(result, dict) else []
            if status == "SUCCESS":
                if denied:
                    # Headless auto-denial (exit 0): warn visibly instead of
                    # ending silently with no output. Note the run ALWAYS
                    # passes --dangerously-skip-permissions (see _base_args),
                    # so never advise re-running with it — point at the
                    # allow-rule / permission mode instead (PR #10 review).
                    return [
                        AgentEvent(
                            type="message",
                            text=(
                                "agy denied headless tool permission for: "
                                + ", ".join(denied)
                                + ". This run already passed "
                                "--dangerously-skip-permissions; the action "
                                "needs an explicit allow-rule "
                                "(permissions.allow in settings.json) or "
                                "CLI-side approval."
                            ),
                            data=data,
                        )
                    ]
                return []  # text already streamed; exit 0 yields done
            text = result.get("message") if isinstance(result, dict) else None
            if not isinstance(text, str) or not text:
                text = f"agy run failed: {status}"
            if denied:
                text += " (denied: " + ", ".join(denied) + ")"
            return [AgentEvent(type="error", text=text, data=data)]

        return [AgentEvent(type="message", text=line, data=data)]
