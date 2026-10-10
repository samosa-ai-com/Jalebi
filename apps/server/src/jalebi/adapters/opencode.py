"""opencode CLI adapter (PRD F4).

Maps `opencode run --format json` (v2.0.26; v1.18 shapes kept as fallback)
events onto the normalized vocabulary:
`step_start` -> step, `tool_use` -> tool_call, `text` -> message, `error` -> error.
Unknown/non-JSON lines are surfaced verbatim as messages (defensive parsing).

v2 notes: `run` no longer accepts `--dir` (working dir comes from PWD, which
the `_spawn` shell wrapper sets via `cd <cwd>`); `--standalone` keeps each run
on a private server so cancel/timeout kills the agent (shared background
service processes survive `killpg`); `--auto` auto-approves permissions that
are not explicitly denied (the worktree gh-deny guard is untouched).
`--thinking` is never passed, so `reasoning` events cannot occur.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle


def _binary() -> str:
    binary = shutil.which("opencode")
    if binary is None:
        raise RuntimeError("opencode CLI not found on PATH")
    return binary


def _spawn(
    args: list[str], cwd: str | Path, env: dict[str, str | None] | None = None
) -> subprocess.Popen[str]:
    # Build the subprocess env FROM the passed env dict (which the queue derives
    # from a filtered os.environ and pins itself). Never start from
    # os.environ.copy() and overlay: that would re-inject keys the caller
    # deliberately removed (e.g. an inherited JALEBI_GITHUB_TOKEN on a task that
    # resolves a different account) — the exact leak that made task 2 act as the
    # wrong account.
    if env is None:
        full_env = os.environ.copy()
    else:
        full_env = {k: v for k, v in env.items() if v is not None}
    full_env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
    # Spawn through a shell: `opencode run --session` stalls when exec'd directly
    # (empty stream, agent loop exits immediately), but works via `sh -c`.
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


class OpenCodeAdapter(AgentAdapter):
    id = "opencode"
    name = "opencode"

    def list_models(self) -> list[str]:
        # v2 boots/uses the server on first call — allow extra time.
        proc = subprocess.run([_binary(), "models"], capture_output=True, text=True, timeout=60)
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
        # No `--dir`: v2 `run` has no such flag; the worktree is entered via
        # the `_spawn` shell wrapper (`cd <cwd>`, so PWD is the worktree).
        # `--standalone`: private server per run so cancel/timeout kills the
        # agent (a shared background service would survive killpg).
        # `--auto`: auto-approve permissions not explicitly denied (the gh-deny
        # guard stays in force; deny rules never raise permission prompts).
        args = [_binary(), "run", "--format", "json", "--standalone", "--auto"]
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
        # Resume from the session's own worktree: on v1, headless
        # ``run --session <id>`` hung with an empty stream when resumed from a
        # different worktree than the session was created in; v2 pins the
        # resume to the session's own directory instead (re-validate live).
        # Jalebi still always resumes from the session's own worktree:
        # pr_review sessions live in the review worktree (`ws/task-<id>-review`,
        # detached at the PR head), so `_run_followup` runs pr_review follow-ups
        # there rather than in the task worktree.
        args = [
            _binary(),
            "run",
            "--format",
            "json",
            "--standalone",
            "--auto",
            "--session",
            session_id,
        ]
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

        if event_type == "text":
            text = (payload.get("part") or {}).get("text")
            return [AgentEvent(type="message", text=text, data=data)]
        if event_type == "tool_use":
            part = payload.get("part") or {}
            state = part.get("state") or {}
            tool_data = {
                "session_id": session_id,
                "tool": part.get("tool"),
                "title": state.get("title"),
                "status": state.get("status"),
                "input": state.get("input"),
                "output": state.get("output"),
            }
            if state.get("status") == "error" and state.get("error"):
                # v2 puts a plain error string here and omits `output`.
                tool_data["error"] = state.get("error")
            return [AgentEvent(type="tool_call", data=tool_data)]
        if event_type == "step_start":
            return [AgentEvent(type="step", phase="step", data=data)]
        if event_type == "step_finish":
            return []
        if event_type == "error":
            err = payload.get("error") or {}
            # v2 shape is {"type": ..., "message": ...}; v1 used
            # {"data": {"message": ...}} / {"name": ...} — try all orders.
            message = (
                err.get("message")
                or (err.get("data") or {}).get("message")
                or err.get("name")
                or "unknown error"
            )
            return [AgentEvent(type="error", text=message, data=data)]

        return [AgentEvent(type="message", text=line, data=data)]
