"""codex CLI adapter (PRD F4).

Maps ``codex exec --json`` / ``codex exec resume <thread_id> --json``
(codex-cli 0.147.0) events onto the normalized vocabulary. Key facts captured
empirically (see docs/03-adapters.md §5):

- JSONL, one object per line. The **resume key is ``thread_id``** from
  ``thread.started`` (there is no ``session_id`` field anywhere).
- **Top-level ``error`` events and ``item``-subtype ``error`` are NOTICES**
  (e.g. "Skill descriptions were shortened…", "Model metadata … not found") —
  they appear even on successful runs, so they map to ``message``, never
  ``error`` (the queue stops the run on the first ``error`` event). The real
  terminal signals are ``turn.failed`` → ``error`` and the process exit code.
- ``exec resume`` has NO ``-s/--cd``: the working dir is set by the
  ``/bin/bash -c 'cd <ws> && exec codex …'`` wrapper (like opencode).
- **Sandbox:** ``-s``/workspace-write needs user namespaces (bwrap). On hosts
  where userns is blocked (this dev machine), ``workspace-write`` cannot write,
  so a cached bwrap probe picks ``sandbox_mode=workspace-write`` when usable and
  ``sandbox_mode=danger-full-access`` otherwise. The mode is applied via
  ``-c key=value`` so it works uniformly on ``exec`` and ``exec resume``.
- ``list_models`` has no CLI command; it reads ``~/.codex/models_cache.json``
  (per-account model list; honors ``$CODEX_HOME``) with a curated fallback.
"""

import functools
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle

# Curated fallback when the per-account models cache is missing/unreadable.
CODE_X_CURATED = ["gpt-5.5", "gpt-5.4-mini", "gpt-5.6-luna", "gpt-5.6-terra"]

# ``bwrap --unshare-all … true`` exercises the same user/net namespaces codex's
# workspace-write sandbox needs. A non-zero exit (e.g. "uid map: Permission
# denied" on a userns-blocked host) means the sandbox cannot work → fall back to
# danger-full-access. Cached per process; never raises.
_SANDBOX_PROBE = [
    "bwrap", "--unshare-all",
    "--ro-bind", "/", "/",
    "--dev", "/dev",
    "--proc", "/proc",
    "true",
]


def _binary() -> str:
    binary = shutil.which("codex")
    if binary is None:
        raise RuntimeError("codex CLI not found on PATH")
    return binary


def _spawn(
    args: list[str], cwd: str | Path, env: dict[str, str | None] | None = None
) -> subprocess.Popen[str]:
    # Build the subprocess env FROM the passed env dict (the queue derives it
    # from a filtered os.environ) — never os.environ.copy()+overlay, so keys the
    # caller deliberately removed cannot leak back in.
    if env is None:
        full_env = os.environ.copy()
    else:
        full_env = {k: v for k, v in env.items() if v is not None}
    # ``exec resume`` has no ``-C/--cd``, so the worktree is entered via the
    # shell wrapper (parity with opencode's spawn quirk workaround).
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


@functools.lru_cache(maxsize=1)
def _sandbox_usable() -> bool:
    """True if codex's ``workspace-write`` sandbox can run on this host.

    workspace-write relies on bubblewrap user namespaces; on hosts where userns
    is blocked, the sandbox starts but cannot write (``bwrap: setting up uid
    map: Permission denied``). Probed once per process; missing bwrap or any
    probe failure → False.
    """
    if shutil.which("bwrap") is None:
        return False
    try:
        proc = subprocess.run(
            _SANDBOX_PROBE, capture_output=True, text=True, timeout=10
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _sandbox_config_args() -> list[str]:
    """``-c`` config overrides selecting the sandbox policy.

    Used on BOTH ``exec`` and ``exec resume`` (resume has no ``-s`` flag). The
    workspace-write variant also enables network (off by default there).
    """
    if _sandbox_usable():
        return [
            "-c", 'sandbox_mode="workspace-write"',
            "-c", "sandbox_workspace_write.network_access=true",
        ]
    return ["-c", 'sandbox_mode="danger-full-access"']


def _unwrap_text(raw: str | None) -> str:
    """Return a friendlier message if ``raw`` is a JSON-encoded error payload.

    ``turn.failed.error.message`` sometimes wraps an OpenAI 400 JSON string.
    One-level unwrap; anything else comes back unchanged. Never raises.
    """
    if not raw:
        return "unknown error"
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(data, dict):
        inner = data.get("message")
        if isinstance(inner, str) and inner:
            return inner
    return raw


def _item_events(item, line: str) -> list[AgentEvent]:
    """Map an ``item.completed`` payload to events (see module docstring)."""
    if not isinstance(item, dict):
        return [AgentEvent(type="message", text=line)]
    subtype = item.get("type")
    text = item.get("text")
    if subtype == "agent_message":
        return [AgentEvent(type="message", text=text or line)]
    if subtype == "reasoning":
        return []  # reasoning traces are noise in the timeline
    if subtype == "command_execution":
        return [
            AgentEvent(
                type="tool_call",
                data={
                    "tool": "command_execution",
                    "command": item.get("command"),
                    "output": item.get("aggregated_output"),
                    "exit_code": item.get("exit_code"),
                    "status": item.get("status"),
                },
            )
        ]
    if subtype == "file_change":
        return [
            AgentEvent(
                type="tool_call",
                data={
                    "tool": "file_change",
                    "changes": item.get("changes"),
                    "status": item.get("status"),
                },
            )
        ]
    if subtype == "mcp_tool_call":
        return [
            AgentEvent(
                type="tool_call",
                data={
                    "tool": "mcp_tool_call",
                    "server": item.get("server"),
                    "tool_name": item.get("tool"),
                    "arguments": item.get("arguments"),
                    "result": item.get("result"),
                    "error": item.get("error"),
                    "status": item.get("status"),
                },
            )
        ]
    if subtype == "error":
        # Item-level error = notice (e.g. "Skill descriptions were shortened"),
        # never a terminal failure.
        return [AgentEvent(type="message", text=item.get("message") or line)]
    if text:
        return [AgentEvent(type="message", text=text)]
    return [AgentEvent(type="message", text=line)]


class CodexAdapter(AgentAdapter):
    id = "codex"
    name = "codex"

    def list_models(self) -> list[str]:
        home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        try:
            with open(home / "models_cache.json") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return sorted(CODE_X_CURATED)
            slugs = [
                m["slug"]
                for m in data.get("models", [])
                if isinstance(m, dict) and m.get("visibility") != "hide" and m.get("slug")
            ]
            if slugs:
                return sorted(set(slugs))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
        return sorted(CODE_X_CURATED)

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        args = [_binary(), "exec", "--json"]
        if model:
            args += ["-m", model]
        args += _sandbox_config_args()
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
        # ``session_id`` is the codex ``thread_id`` UUID; passing it by UUID
        # bypasses codex's cwd-based session filtering.
        args = [_binary(), "exec", "resume", session_id, "--json"]
        if model:
            args += ["-m", model]
        args += _sandbox_config_args()
        args.append(prompt)
        return RunHandle(proc=_spawn(args, cwd, env), parse=self.parse, name=self.name)

    def parse(self, line: str) -> list[AgentEvent]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return [AgentEvent(type="message", text=line)]
        if not isinstance(payload, dict):
            return [AgentEvent(type="message", text=line)]

        etype = payload.get("type")

        if etype == "thread.started":
            # Carries the resume key (thread_id) so RunHandle captures it.
            return [
                AgentEvent(
                    type="step",
                    phase="step",
                    data={"session_id": payload.get("thread_id")},
                )
            ]
        if etype in ("turn.started", "turn.completed", "item.started", "item.updated"):
            return []
        if etype == "item.completed":
            return _item_events(payload.get("item") or {}, line)
        if etype == "turn.failed":
            err = payload.get("error")
            if not isinstance(err, dict):
                err = {}
            return [AgentEvent(type="error", text=_unwrap_text(err.get("message")))]
        if etype == "error":
            # Top-level error = notice (model metadata, skill descriptions, …).
            return [AgentEvent(type="message", text=payload.get("message") or line)]

        return [AgentEvent(type="message", text=line)]
