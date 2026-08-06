# 03 — Agent Adapters

> **Scope:** The `AgentAdapter` interface, CLI command references, output parsing, and known quirks. Update this file for any adapter work (opencode/codex/claude).

---

## 1. Principle (PRD §F4, P0)

The entire system depends on **one interface**. The only place that knows the CLI name is the adapter and a one-line config (`agent.cli`). Switching backend = one-line config change; everything else (queue, git, GitHub, UI, screenings, catalog) is untouched.

## 2. The `AgentAdapter` interface

```ts
interface AgentAdapter {
  id: "opencode" | "codex" | "claude"
  name: string
  listModels(): Promise<string[]>                // models the CLI can use
  start(opts: { cwd; prompt; model?; env? }): Promise<RunHandle>
  resume(opts: { cwd; sessionId; prompt }): Promise<RunHandle>
  parse(line: string): AgentEvent[]              // normalize CLI output → AgentEvent
}

interface RunHandle {
  sessionId: string
  child: ChildProcess
  events: AsyncIterable<AgentEvent>
}
```

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
| **opencode** (v1) | `opencode run --dir <ws> --format json [--model <m>] <prompt>` | `opencode run --session <sessionId> --format json <prompt>` | `--format json` (event stream; contains `session.id`, `session.updated`, `message.updated`, `session.idle`) | `-m/--model provider/model`; env `MODEL` |
| **codex** (later) | `codex exec --json [--model <m>] "<prompt>"` | `codex exec resume <session_id> "<prompt>"` | `--json`; `--output-schema` for structured findings | `-m/--model` (or `config.toml`) |
| **claude** (later) | `claude -p "<prompt>" --output-format stream-json --verbose [--model <m>]` | `claude -p "<prompt>" --resume <session_id> --output-format stream-json` | `--output-format stream-json` (`init.session_id` + typed events) | `--model` |

## 6. Known adapter quirks (document in code + README)

- **opencode:** resuming keeps the session's original model unless `--model` is passed on resume (supported). `--fork` can fork instead of continuing if the user prefers a clean follow-up. Lifecycle status `session.idle` maps to the terminal state `done`.
- **codex:** **on resume, the model/reasoning-effort cannot be changed** — the resumed session retains the original run's settings. Model changes on follow-ups must start a fresh run or be surfaced in the UI.
- **claude:** `--resume <id>` requires the session id captured from the first run (`init.session_id`). `--continue` resumes the last session only (do not rely on it).
- Processes must be spawned with a **working directory = the task worktree** so the CLI discovers `AGENTS.md`/skills.
- Stream output parsing must be **defensive & line-buffered**: consume child `stdout` through a line-buffer interface (e.g. Node `readline`) to reassemble JSON lines split across OS buffer chunks before invoking `parse(line)`. Unknown/non-parseable lines are shown verbatim in the console rather than crashing.

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