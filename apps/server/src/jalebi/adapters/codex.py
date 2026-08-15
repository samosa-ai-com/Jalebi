"""codex CLI adapter (Phase 3).

Scaffolded in Phase 3 Step 1 so the registry and every cli allow-list stay
consistent; the full implementation (codex-cli 0.147.0 command reference in
docs/03-adapters.md §5) lands in Phase 3 Step 2.
"""

from jalebi.adapters.types import AgentAdapter, AgentEvent, RunHandle

_NOT_READY = "codex adapter lands in Phase 3 Step 2"


class CodexAdapter(AgentAdapter):
    id = "codex"
    name = "codex"

    def list_models(self) -> list[str]:
        raise NotImplementedError(_NOT_READY)

    def start(
        self,
        cwd: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        raise NotImplementedError(_NOT_READY)

    def resume(
        self,
        cwd: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        env: dict[str, str | None] | None = None,
    ) -> RunHandle:
        raise NotImplementedError(_NOT_READY)

    def parse(self, line: str) -> list[AgentEvent]:
        raise NotImplementedError(_NOT_READY)
