"""Agent backend registry: every cli allow-list derives from here (PRD F4).

The backend for any action is chosen per task/screen/agent (each form has a
Backend select); the global Settings ``default_backend`` is the fallback. The
registry is the single source of truth for available backends — ``available_adapters()``
drives every cli allow-list so they can never drift from the implemented adapters.
"""

import re
import shutil
import subprocess

from jalebi.adapters.agy import AgyAdapter
from jalebi.adapters.claude import ClaudeAdapter
from jalebi.adapters.cline import ClineAdapter
from jalebi.adapters.codex import CodexAdapter
from jalebi.adapters.commandcode import CommandCodeAdapter
from jalebi.adapters.grok import GrokAdapter
from jalebi.adapters.kilo import KiloAdapter
from jalebi.adapters.opencode import OpenCodeAdapter
from jalebi.adapters.pi import PiAdapter
from jalebi.adapters.qwen import QwenAdapter
from jalebi.adapters.types import AgentAdapter

ADAPTERS: dict[str, AgentAdapter] = {
    "opencode": OpenCodeAdapter(),
    "codex": CodexAdapter(),
    "claude": ClaudeAdapter(),
    "pi": PiAdapter(),
    "kilo": KiloAdapter(),
    "qwen": QwenAdapter(),
    "cline": ClineAdapter(),
    "grok": GrokAdapter(),
    "commandcode": CommandCodeAdapter(),
    "agy": AgyAdapter(),
}


def get_adapter(cli: str) -> AgentAdapter:
    try:
        return ADAPTERS[cli]
    except KeyError as exc:
        raise ValueError(f"unknown agent cli: {cli}") from exc


def available_adapters() -> list[str]:
    return sorted(ADAPTERS)


def is_backend_available(cli: str) -> bool:
    """True when the backend is implemented AND its CLI binary is on PATH.

    Binary names match cli ids for every adapter (see each ``_binary()``).
    Used to fail fast with a clear message instead of a spawn crash when an
    enabled backend isn't installed on this machine.
    """
    return cli in ADAPTERS and shutil.which(cli) is not None


# CLI versions the adapters were validated against (Sep 2026 smoke runs +
# doc capture). Compared by ``cli_version`` for the health endpoint; a drift
# only warns — newer CLIs usually still work, and parsers degrade to
# verbatim text rather than crashing.
VERIFIED_VERSIONS: dict[str, str] = {
    "opencode": "1.18",
    "codex": "0.147.0",
    "claude": "2.1.233",
    "pi": "0.80.2",
    "kilo": "7.5.16",
    "qwen": "0.23.1",
    "cline": "3.0.61",
    "grok": "1.0.13",
    "commandcode": "1.50.1",
    "agy": "1.2.2",
}

_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


def cli_version(cli: str) -> str | None:
    """Best-effort installed CLI version (``<bin> --version``), else None."""
    if cli not in ADAPTERS:
        return None
    try:
        proc = subprocess.run(
            [cli, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    match = _VERSION_RE.search(proc.stdout + "\n" + proc.stderr)
    return match.group(0) if match else None
