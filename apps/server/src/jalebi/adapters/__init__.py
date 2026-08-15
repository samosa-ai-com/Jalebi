"""Agent backend registry: one-line `agent.cli` switch (PRD F4).

The registry is the single source of truth for available backends — every cli
allow-list (`ALLOWED_AGENT_CLIS`, catalog ``ALLOWED_CLIS``, task/screening cli
validation) derives from ``available_adapters()`` so the lists can never drift
from the implemented adapters.
"""

from jalebi.adapters.claude import ClaudeAdapter
from jalebi.adapters.codex import CodexAdapter
from jalebi.adapters.opencode import OpenCodeAdapter
from jalebi.adapters.types import AgentAdapter

ADAPTERS: dict[str, AgentAdapter] = {
    "opencode": OpenCodeAdapter(),
    "codex": CodexAdapter(),
    "claude": ClaudeAdapter(),
}


def get_adapter(cli: str) -> AgentAdapter:
    try:
        return ADAPTERS[cli]
    except KeyError as exc:
        raise ValueError(f"unknown agent cli: {cli}") from exc


def available_adapters() -> list[str]:
    return sorted(ADAPTERS)
