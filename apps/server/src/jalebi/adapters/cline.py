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
  ``--list-models``/``models`` subcommand exists and ``config --json`` needs
  a TTY, so the catalog is harvested from the installed bundle's embedded
  model maps (cached by mtime; curated verified ids first, curated-only on
  any bundle problem).
- Terminal line is ``run_result`` (``finishReason:"completed"`` + exit 0, or
  ``"error"`` + exit 1). `done` events carry reason/text/iterations.
- **Session id is NOT in the stdout stream** — the resume key lives in
  ``cline history --json`` (latest entry). ``resolve_session`` performs that
  post-run lookup so the queue can persist the id and follow-ups can resume.
- Live resume (``--id`` + prompt) and diff shapes were NOT live-exercised —
  mapped per docs, marked UNVERIFIED.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle

# Curated free-tier ids — verified present in the installed 3.0.61 bundle.
# Listed first by list_models(); the rest of the bundle catalog follows.
CLINE_CURATED = [
    "z-ai/glm-5.3-flash",
    "z-ai/glm-5.3-free",
    "z-ai/glm-5.2-free",
    "z-ai/glm-4.7-flash-free",
]

# Catalog entries in the installed bundle look like
# `"z-ai/glm-5.3-flash":{id:"z-ai/glm-5.3-flash",name:"GLM-5.3-Flash",…}` and
# sit in dense per-provider maps (dual ESM/CJS builds embed the same map
# twice — verified identical). Entries > _MAP_GAP bytes apart start a new map.
_CATALOG_ENTRY_RE = re.compile(rb'"([A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+)":\{id:"')
_MAP_GAP = 5000

# Bundle path → (mtime, harvested ids). The scan runs only when the installed
# bundle changes (install/upgrade), not on every /api/models call.
_bundle_cache: dict[str, tuple[float, list[str]]] = {}


def _bundle_path() -> Path | None:
    which = shutil.which("cline")
    if not which:
        return None
    try:
        candidate = Path(which).resolve().parent / ".cline"
    except OSError:
        return None
    return candidate if candidate.is_file() else None


def _harvest_bundle_models(bundle: Path) -> list[str]:
    """Ids of the largest dense catalog map in the bundle file."""
    try:
        data = bundle.read_bytes()
    except OSError:
        return []
    maps: list[list[str]] = []
    current: list[str] = []
    prev: int | None = None
    for match in _CATALOG_ENTRY_RE.finditer(data):
        pos = match.start()
        if prev is not None and pos - prev > _MAP_GAP:
            if current:
                maps.append(current)
            current = []
        mid = match.group(1).decode()
        if mid not in current:
            current.append(mid)
        prev = pos
    if current:
        maps.append(current)
    if not maps:
        return []
    return max(maps, key=len)


def _bundle_models() -> list[str]:
    bundle = _bundle_path()
    if bundle is None:
        return []
    try:
        mtime = bundle.stat().st_mtime
    except OSError:
        return []
    cached = _bundle_cache.get(str(bundle))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    models = _harvest_bundle_models(bundle)
    _bundle_cache[str(bundle)] = (mtime, models)
    return models


def _latest_history_session(cwd: str) -> str | None:
    """Latest session id from ``cline history --json`` (resume key lookup).

    The session id is not carried on the stdout stream, so after a run
    finishes we ask the CLI's history (newest entry first) for it. Shape is
    parsed defensively (list of entries, or a dict wrapping them) because the
    history wire format was not live-verified. Returns None on any problem —
    the follow-up route then falls back to its own no-session handling.
    """
    try:
        proc = subprocess.run(
            [_binary(), "history", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        for key in ("history", "sessions", "entries"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        return None
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        for key in ("id", "sessionId", "session_id"):
            sid = entry.get(key)
            if isinstance(sid, str) and sid:
                return sid
    return None


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
    if etype == "content_update":
        # Tool progress pings (live shape from a task-69 run): empty chunks
        # (stream-open/detachable notices) are noise; real stdout/stderr
        # chunks become messages (the queue merges consecutive ones).
        if event.get("contentType") == "tool":
            update = event.get("update")
            if not isinstance(update, dict):
                update = {}
            chunk = update.get("chunk")
            if isinstance(chunk, str) and chunk:
                return [AgentEvent(type="message", text=chunk)]
            return []
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
        # No list command and `config --json` needs a TTY, so the catalog
        # comes from the installed bundle (cached by mtime): curated ids
        # first (verified working), then the largest catalog map. Harvested
        # entries are unattributed — the bundle holds many providers'
        # catalogs, so an id may fail under the configured provider (clean
        # exit-1 model error, never silent corruption). Any bundle problem
        # falls back to the curated list; `adapter_model_lists` still wins.
        models = list(CLINE_CURATED)
        for mid in _bundle_models():
            if mid not in models:
                models.append(mid)
        return models

    def resolve_session(self, cwd: str) -> str | None:
        # Session id is NOT in the stdout stream — resolve it from
        # `cline history --json` after the run (see module docstring).
        return _latest_history_session(cwd)

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        # No wall-clock cap: the queue's per-task timeout + stall watchdog own
        # the deadline (60 min default, escalated retries).
        args = [_binary(), "--json", "--yolo", "--cwd", str(cwd)]
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
        # docstring). Mirrors start argv + session pick. No wall-clock cap: the
        # queue's per-task timeout owns the deadline.
        args = [_binary(), "--json", "--yolo", "--cwd", str(cwd),
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
