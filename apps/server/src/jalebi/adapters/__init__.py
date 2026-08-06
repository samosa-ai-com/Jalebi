"""Agent backend registry: one-line `agent.cli` switch (PRD F4)."""

from jalebi.adapters.opencode import OpenCodeAdapter
from jalebi.adapters.types import AgentAdapter

ADAPTERS: dict[str, AgentAdapter] = {
    "opencode": OpenCodeAdapter(),
}


def get_adapter(cli: str) -> AgentAdapter:
    try:
        return ADAPTERS[cli]
    except KeyError as exc:
        raise ValueError(f"unknown agent cli: {cli}") from exc


def available_adapters() -> list[str]:
    return sorted(ADAPTERS)
