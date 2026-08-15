"""claude (Claude Code) CLI adapter (PRD F4).

Maps ``claude -p … --output-format stream-json --verbose`` (claude 2.1.233)
events onto the normalized vocabulary. Facts captured empirically + from the
official SDK reference (see docs/03-adapters.md §5):

- JSONL, one object per line. The **resume key is ``session_id``** (UUID) from
  the first ``system``/``init`` line. That payload is huge (tools,
  slash_commands, skills, ``apiKeySource``) and is never echoed.
- ``system`` subtypes other than ``init`` (``api_retry`` repeats with
  attempt/max_retries, ``auth_status``, …) are transient noise → silent. The
  terminal signal is the final ``result`` line and/or the process exit code.
- **``result`` discriminator is ``is_error``, not ``subtype``** — the no-auth
  failure keeps ``subtype:"success"`` with ``is_error:true`` and
  ``result:"Not logged in · Please run /login"`` (exit 1, nothing on stderr).
- ``assistant``/``user``/``system`` never emit ``done``/``error``; success ends
  via the exit code (RunHandle yields ``done`` on exit 0).
- **Auth env:** claude authenticates from the inherited ``ANTHROPIC_AUTH_TOKEN``
  → ``ANTHROPIC_API_KEY`` → OAuth chain; ``_build_agent_env`` passes
  ``ANTHROPIC_*`` through unchanged (Jalebi never touches it) — desired.
- **``--permission-mode bypassPermissions``** = parity with opencode's bash
  ``"*": "allow"``: ``default`` hangs on permission prompts, ``dontAsk``
  auto-denies everything; deny rules from a worktree ``.claude/settings.json``
  still apply in every mode.
- claude reads ``CLAUDE.md``, not ``AGENTS.md`` (the worktree bootstrap writes
  it — separate guard step).
"""

import json
import os
import shlex
import shutil
import subprocess

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle

# Curated alias list — stable across installs (full model names vary by
# account/proxy). Overridable via the `adapter_model_lists` setting.
CLAUDE_CURATED = ["default", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]", "best", "fable"]

# claude's verbatim no-auth failure message (exit 1; subtype stays "success").
NO_AUTH_RESULT = "Not logged in · Please run /login"


def _binary() -> str:
    binary = shutil.which("claude")
    if binary is None:
        raise RuntimeError("claude CLI not found on PATH")
    return binary


def _spawn(
    args: list[str], cwd: str | os.PathLike[str], env: dict[str, str | None] | None = None
) -> subprocess.Popen[str]:
    # Env built FROM the passed dict (None values dropped) — never
    # os.environ.copy()+overlay, so deliberately-removed keys cannot leak back.
    if env is None:
        full_env = os.environ.copy()
    else:
        full_env = {k: v for k, v in env.items() if v is not None}
    # claude -p has no --cd; enter the worktree via the shell wrapper (parity
    # with the opencode/codex spawn quirk workaround).
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
    """Map a ``message.content[]`` array to events. ``content`` may be non-list."""
    if not isinstance(content, list):
        return []
    events: list[AgentEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if user:
            # Prompt echo → silent; tool results → visible (output parity with
            # codex's command_execution).
            if btype == "tool_result":
                events.append(
                    AgentEvent(
                        type="tool_call",
                        data={
                            "tool": "tool_result",
                            "tool_use_id": block.get("tool_use_id"),
                            "output": _join_text(block.get("content")),
                            "is_error": block.get("is_error"),
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


def _result_events(payload: dict) -> list[AgentEvent]:
    """Map the final ``result`` line. ``is_error`` is the discriminator, not the subtype."""
    subtype = payload.get("subtype") or ""
    is_error = payload.get("is_error") is True or str(subtype).startswith("error_")
    if not is_error:
        final = payload.get("result")
        if isinstance(final, str) and final:
            return [AgentEvent(type="message", text=final)]
        return []
    text = payload.get("result")
    if not isinstance(text, str) or not text:
        errors = payload.get("errors")
        if isinstance(errors, list):
            text = "\n".join(str(e) for e in errors if isinstance(e, str))
        if not text and isinstance(payload.get("terminal_reason"), str):
            text = payload["terminal_reason"]
        text = text or "claude run failed"
    return [AgentEvent(type="error", text=text)]


class ClaudeAdapter(AgentAdapter):
    id = "claude"
    name = "claude"

    def list_models(self) -> list[str]:
        # No `claude models` command; aliases are stable. Authoring order kept.
        return list(CLAUDE_CURATED)

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        args = [
            _binary(),
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",  # required by stream-json
            "--permission-mode",
            "bypassPermissions",
        ]
        if model:
            args += ["--model", model]
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def resume(
        self,
        cwd: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        args = [
            _binary(),
            "-p",
            prompt,
            "--resume",
            session_id,  # UUID from system/init
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
        ]
        if model:
            args += ["--model", model]
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def parse(self, line: str) -> list[AgentEvent]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return [AgentEvent(type="message", text=line)]
        if not isinstance(payload, dict):
            return [AgentEvent(type="message", text=line)]

        etype = payload.get("type")

        if etype == "system":
            if payload.get("subtype") == "init":
                # Carries the resume key (session_id); payload is never echoed.
                return [
                    AgentEvent(
                        type="step",
                        phase="step",
                        data={"session_id": payload.get("session_id")},
                    )
                ]
            return []
        if etype in ("user", "assistant"):
            msg = payload.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            return _content_events(content, user=(etype == "user"))
        if etype == "result":
            return _result_events(payload)

        return [AgentEvent(type="message", text=line)]
