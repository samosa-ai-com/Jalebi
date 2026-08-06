# 03 — Agent Adapters

> **Scope:** The `AgentAdapter` interface, CLI command references, output parsing, and known quirks. Update this file for any adapter work (opencode/codex/claude).

---

## 1. Principle (PRD §F4, P0)

The entire system depends on **one interface**. The only place that knows the CLI name is the adapter and a one-line config (`agent.cli`). Switching backend = one-line config change; everything else (queue, git, GitHub, UI, screenings, catalog) is untouched.

## 2. The `AgentAdapter` interface

Python equivalent (`src/jalebi/adapters/types.py`):

```python
class AgentAdapter:
    id: str          # "opencode" | "codex" | "claude"
    name: str
    def list_models(self) -> list[str]                          # models the CLI can use
    def start(self, cwd, prompt, model=None, env=None) -> RunHandle
    def resume(self, cwd, session_id, prompt) -> RunHandle
    def parse(self, line: str) -> list[AgentEvent]              # normalize CLI output → AgentEvent

@dataclass
class RunHandle:
    proc: Any                   # duck-typed: .stdout, .stderr, .wait()
    parse: Callable[[str], list[AgentEvent]]
    session_id: str | None      # captured from events
    def events(self) -> Iterator[AgentEvent]   # streams parsed events; ends with done/error
```

Registry (`src/jalebi/adapters/__init__.py`): `get_adapter(cli)`. `agent.cli` setting (default `"opencode"`) is the one-line switch.

## 3. `AgentEvent` normalized vocabulary

| Event | Meaning |
|-------|---------|
| `step` | Phase transition (scanning/planning/implementing/testing/creating-pr/reviewing) |
| `tool_call` | Agent invoked a tool (file edit, bash, grep, …) |
| `message` | Agent text output |
| `diff` | File-change delta (used for the diff viewer) |
| `done` / `error` | Terminal states |

## 4. Backend selection

`agent.cli` in config/Settings: `"opencode" | "codex" | "claude"`. One line.

## 5. CLI command references (authoritative, captured 2026-08)

| CLI | Start a new task | Resume (follow-up) | Structured output | Model flag |
|-----|------------------|--------------------|-------------------|------------|
| **opencode** (v1.18) | `opencode run --dir <ws> --format json [--model <m>] <prompt>` | `opencode run --session <sessionId> --format json <prompt>` | `--format json` — newline-delimited events (see mapping below) | `-m/--model provider/model` |
| **codex** (later) | `codex exec --json [--model <m>] "<prompt>"` | `codex exec resume <session_id> "<prompt>"` | `--json`; `--output-schema` for structured findings | `-m/--model` (or `config.toml`) |
| **claude** (later) | `claude -p "<prompt>" --output-format stream-json --verbose [--model <m>]` | `claude -p "<prompt>" --resume <session_id> --output-format stream-json` | `--output-format stream-json` (`init.session_id` + typed events) | `--model` |

### opencode JSON event mapping (v1.18 — differs from older docs)

Real `--format json` top-level `type` values and the adapter mapping (field is **`sessionID`**, capital D):

| CLI event | → `AgentEvent` | Notes |
|-----------|----------------|-------|
| `step_start` | `step` (phase `"step"`) | per agent turn, not the PRD phase vocabulary |
| `tool_use` | `tool_call` | `data`: tool, title, status, input, output |
| `text` | `message` | `part.text` |
| `step_finish` | — (silent) | reason `"stop"`/`"tool-calls"`; process exit is the terminal signal |
| `error` | `error` | `error.data.message` |
| non-JSON / unknown | `message` (verbatim) | defensive: never crash |

> **CLI drift (PRD risk #1):** the PRD-documented events (`session.id`, `message.updated`, `session.idle`) no longer match opencode v1.18. `done` is emitted on process exit (code 0), `error` on non-zero (with stderr tail).

## 6. Known adapter quirks (document in code + README)

- **opencode:** resuming keeps the session's original model unless `--model` is passed on resume (supported). `--fork` can fork instead of continuing. `OPENCODE_DISABLE_AUTOUPDATE=1` is set on spawn.
- **opencode (spawn quirk, observed):** `opencode run --session <id>` **stalls with an empty stream when exec'd directly** by `subprocess.Popen` (the agent loop exits immediately after step 1), but runs correctly when spawned through a shell. The adapter therefore wraps every command in `/bin/bash -c 'cd <worktree> && exec opencode …'` (arguments are `shlex`-quoted). `--dir` starts are unaffected by the direct-spawn bug but use the same wrapper for consistency.
- **codex:** **on resume, the model/reasoning-effort cannot be changed** — the resumed session retains the original run's settings. Model changes on follow-ups must start a fresh run or be surfaced in the UI.
- **claude:** `--resume <id>` requires the session id captured from the first run (`init.session_id`). `--continue` resumes the last session only (do not rely on it).
- Processes must be spawned with a **working directory = the task worktree** so the CLI discovers `AGENTS.md`/skills.
- Stream output parsing is **defensive & line-buffered**: iterate child `stdout` line-by-line (`text=True, bufsize=1`); unknown/non-parseable lines are shown verbatim in the console rather than crashing. `stderr` is drained in a background thread (bounded tail) to avoid pipe deadlock and to report exit failures.

## 7. Personality & skills injection (PRD §F6)

- Each catalog agent's files live in `<jalebi-data>/agents/<agentId>/` containing `personality.md` (or `AGENTS.md` fragment) and `skills/*.md`.
- When a task uses a catalog agent, the orchestrator:
  1. writes/merges the personality into the **task worktree's `AGENTS.md`** (root level, so the CLI auto-discovers it), and
  2. copies the skill files into the worktree (e.g. `.jalebi/agents/<agentId>/skills/*.md`) and **references them from `AGENTS.md` via `@path` links**, plus passes the **directory path** on the run command where the CLI supports it.
- The **default agent of the CLI** (e.g. opencode's build agent) reads `AGENTS.md` + skills and uses them opportunistically. No custom agent definitions, no special prompts plumbing.
- For opencode this also works with its `.claude/skills`-compatible loading (`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` must remain unset) and `AGENTS.md` auto-discovery.
- Because the mechanism is *files in the worktree*, it works identically for opencode, Codex (`AGENTS.md`), and Claude (`CLAUDE.md`) — the adapter only varies the file/dir conventions.

## 8. Model selection (PRD §F5)

- No hard-coded model. Each task exposes a model dropdown populated from the active adapter's `listModels()`.
- Per-task default: the adapter's configured default.
- A catalog agent may pin a model.
- UI shows the model used per task/run; follow-ups reuse the run's model by default but allow override where the CLI permits it (see §6 quirks).