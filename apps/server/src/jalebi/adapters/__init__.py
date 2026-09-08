"""Agent backend registry: every cli allow-list derives from here (PRD F4).

The backend for any action is chosen per task/screen/agent (each form has a
Backend select); the global Settings ``default_backend`` is the fallback. The
registry is the single source of truth for available backends — ``available_adapters()``
drives every cli allow-list so they can never drift from the implemented adapters.
"""

from jalebi.adapters.agy import AgyAdapter
from jalebi.adapters.claude import ClaudeAdapter
from jalebi.adapters.cline import ClineAdapter
from jalebi.adapters.codex import CodexAdapter
from jalebi.adapters.commandcode import CommandCodeAdapter
from jalebi.adapters.goose import GooseAdapter
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
    "goose": GooseAdapter(),
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
