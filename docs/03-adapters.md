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
    def resume(self, cwd, session_id, prompt, model=None) -> RunHandle
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
| **opencode** (v1.18) | `opencode run --dir <ws> --format json [--model <m>] <prompt>` | `opencode run --dir <ws> --session <sessionId> --format json [--model <m>] <prompt>` | `--format json` — newline-delimited events (see mapping below) | `-m/--model provider/model` |
| **codex** (codex-cli 0.147.0) | `codex exec --json [-m <model>] [-s read-only\|workspace-write\|danger-full-access] [-c key=value] "<prompt>"` | `codex exec resume <thread_id> --json [-m <model>] [-c key=value] "<prompt>"` | `--json` — JSONL events (see mapping below); `--output-schema <file>` for a structured final shape | `-m/--model` (honored on resume; without it the original session model is kept). No model-list command. |
| **claude** (claude 2.1.233) | `claude -p "<prompt>" --output-format stream-json --verbose [--model <m>] [--permission-mode <mode>]` | `claude -p "<prompt>" --resume <session_id> --output-format stream-json --verbose [--model <m>]` | `--output-format stream-json` **requires `--verbose`**; `--json-schema <schema>` (print-mode) for structured output | `--model` (honored on resume; `--fork-session` = new session id) |

> **Version drift (captured 2026-08):** the codex/claude rows above were verified against the locally installed binaries — **codex-cli 0.147.0** and **claude 2.1.233** — and supersede the older PRD §F4 table (which omitted the resume model flag for codex and showed a bare `init` first line for claude). The opencode v1.18 row is current and unchanged. Re-verify both rows after any CLI upgrade.

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

### codex JSONL event mapping (0.147.0)

Top-level JSON `type` values (one object per line) and the adapter mapping. The **resume key is `thread_id`** (UUID, from `thread.started`) — there is no `session_id` field:

| CLI event | → `AgentEvent` | Notes |
|-----------|----------------|-------|
| `thread.started` | — (capture `thread_id`) | first event; `thread_id` is the resume key |
| `turn.started` | — (silent) | |
| `item.started` / `item.updated` | — (silent) | carry partial state |
| `item.completed` | depends on item subtype (below) | text/tool/file content |
| `turn.completed` | — (silent) | carries `usage` |
| `turn.failed` / `error` | `error` | `error.message` |
| non-JSON / unknown | `message` (verbatim) | defensive: never crash |

`item.completed` item subtypes:

| item subtype | → `AgentEvent` | Notes |
|--------------|----------------|-------|
| `agent_message` | `message` | `item.text` |
| `reasoning` | — (silent) | reasoning traces are noise in the timeline |
| `command_execution` | `tool_call` | `data`: command, aggregated_output, exit_code, status |
| `file_change` | `tool_call` | `data`: changes (path/kind), status |
| `mcp_tool_call` | `tool_call` | `data`: server, tool, arguments, result/error, status |
| `error` | `error` | `item.message` |

### claude stream-json mapping (2.1.233)

Top-level JSON `type` values (one object per line, `--verbose` required) and the adapter mapping:

| CLI event | → `AgentEvent` | Notes |
|-----------|----------------|-------|
| `system` (subtype `init`) | — (capture `session_id`) | first line; carries `session_id`, `model`, `permissionMode` |
| `system` (subtype `api_retry`/…, `auth_status`) | — (silent) | retries/status; a terminal `api_error` surfaces via the final `result` |
| `user` | — (silent) | echo of the prompt + `tool_result` blocks |
| `assistant` | `step` + `message`/`tool_call` | `message.content[]`: `text`→message, `tool_use`→tool_call, `thinking`→silent |
| `stream_event` | — (silent unless partials wanted) | only with `--include-partial-messages` |
| `result` (subtype `success`) | `done` | `result` = final text (also surfaced as a final `message`); `session_id` present |
| `result` (error subtypes) | `error` | e.g. `error_max_turns`, `error_during_execution`, `error_max_budget_usd`; `errors[]` carries details |
| non-JSON / unknown | `message` (verbatim) | defensive: never crash |

> **claude auth failure (observed):** with no auth configured, `claude -p` exits 1 and emits a final `result` line with `"result":"Not logged in · Please run /login"` and `terminal_reason:"api_error"` on stdout (nothing on stderr). The adapter maps that to a clear `error` event.

## 6. Known adapter quirks (document in code + README)

- **opencode:** resuming keeps the session's original model unless `--model` is passed on resume (supported). The adapter's `resume(…, model=…)` appends `--model` when set, so the follow-up Model dropdown is honored. `--fork` can fork instead of continuing. `OPENCODE_DISABLE_AUTOUPDATE=1` is set on spawn.
- **opencode (spawn quirk, observed):** `opencode run --session <id>` **stalls with an empty stream when exec'd directly** by `subprocess.Popen` (the agent loop exits immediately after step 1), but runs correctly when spawned through a shell. The adapter therefore wraps every command in `/bin/bash -c 'cd <worktree> && exec opencode …'` (arguments are `shlex`-quoted). `--dir` starts are unaffected by the direct-spawn bug but use the same wrapper for consistency.
- **opencode (resume directory mismatch, observed Aug 2026):** headless `opencode run --session <id>` **hangs forever when resumed from a different worktree than the one the session was created in** — the model stream comes back empty, opencode logs `exiting loop`, and the process never exits. `resume` therefore passes `--dir <cwd>` (parity with `start`) **and** Jalebi always resumes from the session's own worktree: pr_review sessions live in the review worktree (`ws/task-<id>-review`, detached at the PR head), so `_run_followup` runs pr_review follow-ups there rather than in the task worktree. This is a hard requirement, not a nicety — resuming from the wrong worktree silently produces a run that stays `running` with an empty timeline (see `docs/06-task-queue.md` §4 for the stall guard that bounds it anyway).
- **codex:** `-m/--model` is **honored on resume** in 0.147.0 (source-verified: a resume without `-m` keeps the original session model; with `-m` it switches). `exec resume` has **no `-C/--cd`** — the cwd is wherever you launch it from, so the adapter uses the same `/bin/bash -c 'cd <worktree> && exec …'` shell wrapper as opencode. `exec` requires a git repo (or `--skip-git-repo-check`). **Sandbox:** `-s workspace-write` confines writes to the workspace but has **no network by default** — pass `-c sandbox_workspace_write.network_access=true` for parity with the other adapters' network access. `--dangerously-bypass-approvals-and-sandbox` is the fully-open fallback. **Auth:** reuses `~/.codex/auth.json` automatically; `CODEX_API_KEY` works for `exec` only; `OPENAI_API_KEY` is **not** read at runtime (only via `codex login --with-api-key`). There is **no model-list command** — `list_models` uses the curated list + `adapter_model_lists` override.
- **claude:** `--resume <id>` requires the session id captured from the first run (the `system/init` `session_id`). `--continue` resumes the last session only (do not rely on it). `--fork-session` (with `--resume`) creates a **new** session id. `--model` is honored on resume. `--output-format stream-json` **requires `--verbose`**. Auth precedence: `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` → OAuth; no auth → exit 1 with `"result":"Not logged in · Please run /login"`. **Claude reads `CLAUDE.md`, not `AGENTS.md`** — a claude-run worktree must also carry the Jalebi rules in `CLAUDE.md` (see §7).
- Processes must be spawned with a **working directory = the task worktree** so the CLI discovers `AGENTS.md`/skills.
- Stream output parsing is **defensive & line-buffered**: iterate child `stdout` line-by-line (`text=True, bufsize=1`); unknown/non-parseable lines are shown verbatim in the console rather than crashing. `stderr` is drained in a background thread (bounded tail) to avoid pipe deadlock and to report exit failures.

## 7. Personality & skills injection (PRD §F6)

- Each catalog agent's files live in `<jalebi-data>/agents/<agentId>/` containing `personality.md` (or `AGENTS.md` fragment) and `skills/*.md`.
- When a task uses a catalog agent, the orchestrator:
  1. writes/merges the personality into the **task worktree's `AGENTS.md`** (root level, so the CLI auto-discovers it), and
  2. copies the skill files into the worktree (e.g. `.jalebi/agents/<agentId>/skills/*.md`) and **references them from `AGENTS.md` via `@path` links**, plus passes the **directory path** on the run command where the CLI supports it.
- The **default agent of the CLI** (e.g. opencode's build agent) reads `AGENTS.md` + skills and uses them opportunistically. No custom agent definitions, no special prompts plumbing.
- For opencode this also works with its `.claude/skills`-compatible loading (`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` must remain unset) and `AGENTS.md` auto-discovery.
- Because the mechanism is *files in the worktree*, it works for opencode and Codex (both read `AGENTS.md`). **Claude reads `CLAUDE.md`, not `AGENTS.md`** — a claude-backed run therefore also gets the Jalebi rules written to `CLAUDE.md` (merged via the same marker mechanism), so the isolation contract holds for every backend. *(The `CLAUDE.md` write lands with the Step 3 guard work in `worktree_bootstrap`.)*

## 8. Model selection (PRD §F5)

- No hard-coded model. Each task exposes a model dropdown populated from the active adapter's `listModels()`.
- Per-task default: the adapter's configured default.
- A catalog agent may pin a model.
- UI shows the model used per task/run; follow-ups reuse the run's model by default but allow override where the CLI permits it (see §6 quirks).

## 9. Agent subprocess environment

- `start`/`resume` accept an `env` dict; `None` values **remove** the key from the inherited environ (used to strip `GH_TOKEN`/`GITHUB_TOKEN`/a leaked `JALEBI_GITHUB_TOKEN`).
- The queue builds the env via `_build_agent_env(token)`: `JALEBI_GITHUB_TOKEN` = the **selected account's** PAT for all task types (GitHub API use), but **no git push credentials** (`auth_env` is never applied — Jalebi is the only pusher). `GIT_AUTHOR_*`/`GIT_COMMITTER_*` = `Jalebi <jalebi@localhost>` always, plus the `gh`-neutralization vars. Inherited `GIT_CONFIG_*`/`GIT_DIR` state is stripped and `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` are pinned, so the parent shell's git config cannot hijack or redirect the agent's git.
- `_spawn` builds the subprocess env **from the passed env dict** (not `os.environ.copy()` + overlay), so a key the queue deliberately removed can never leak back in from the server's environment.
- `resume` now forwards `env` too (previously dropped it) — follow-ups get the same credentials/guards as the original run.
