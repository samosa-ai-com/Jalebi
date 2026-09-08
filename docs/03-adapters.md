# 03 — Agent Adapters

> **Scope:** The `AgentAdapter` interface, CLI command references, output parsing, and known quirks. Update this file for any adapter work (opencode/codex/claude).

---

## 1. Principle (PRD §F4, P0)

The entire system depends on **one interface**. The only place that knows the CLI name is the adapter. The backend for any action is chosen per task/screen/agent (each form has a Backend select); the global `default_backend` setting is the fallback. Everything else (queue, git, GitHub, UI, screenings, catalog) is untouched by a backend change.

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

Registry (`src/jalebi/adapters/__init__.py`): `get_adapter(cli)`. `default_backend` (default `"opencode"`) is the global fallback used when an action has no backend of its own.

## 3. `AgentEvent` normalized vocabulary

| Event | Meaning |
|-------|---------|
| `step` | Phase transition (scanning/planning/implementing/testing/creating-pr/reviewing) |
| `tool_call` | Agent invoked a tool (file edit, bash, grep, …) |
| `message` | Agent text output |
| `diff` | File-change delta (used for the diff viewer) |
| `done` / `error` | Terminal states |

## 4. Backend selection

The backend is chosen **per action**, not globally. Every task/follow-up/screen/
catalog-agent form has its own **Backend** select (`"opencode" | "codex" |
"claude"`); the global Settings `default_backend` (default `"opencode"`) is only
the fallback for operations that don't pick one (triggered tasks, legacy rows,
unpinned screens). Run-time resolution everywhere: `X.cli or default_backend or
"opencode"`. A follow-up whose backend differs from the session's own can't
resume it (each CLI owns its session format) — it starts a fresh run seeded with
the prior conversation (see §6, and `queue._run_followup`).

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
| `thread.started` | `step` (phase `"step"`) | first event; `thread_id` is the resume key, carried in `data.session_id` |
| `turn.started` | — (silent) | |
| `item.started` / `item.updated` | — (silent) | carry partial state |
| `item.completed` | depends on item subtype (below) | text/tool/file content |
| `turn.completed` | — (silent) | carries `usage` |
| `turn.failed` | `error` | the real failure signal; `error.message` (JSON-unwrapped) |
| `error` | `message` (notice) | **NOT terminal** — "Skill descriptions were shortened…" / "Model metadata … not found" appear even on success |
| non-JSON / unknown | `message` (verbatim) | defensive: never crash |

`item.completed` item subtypes:

| item subtype | → `AgentEvent` | Notes |
|--------------|----------------|-------|
| `agent_message` | `message` | `item.text` |
| `reasoning` | — (silent) | reasoning traces are noise in the timeline |
| `command_execution` | `tool_call` | `data`: command, aggregated_output, exit_code, status |
| `file_change` | `tool_call` | `data`: changes (path/kind), status |
| `mcp_tool_call` | `tool_call` | `data`: server, tool, arguments, result/error, status |
| `error` | `message` (notice) | `item.message` — never terminal |

### claude stream-json mapping (2.1.233)

Top-level JSON `type` values (one object per line, `--verbose` required) and the adapter mapping:

| CLI event | → `AgentEvent` | Notes |
|-----------|----------------|-------|
| `system` (subtype `init`) | `step` (phase `"step"`) | first line; `session_id` is the resume key (UUID); the giant payload (tools, slash_commands, skills, `apiKeySource`) is never echoed |
| `system` (subtype `api_retry`/…, `auth_status`) | — (silent) | transient; `api_retry` repeats with `attempt`/`max_retries` and is non-fatal — a terminal `api_error` surfaces only via the final `result` |
| `user` | `tool_call` per `tool_result` block | tool output echo (`data`: tool_use_id/output/is_error); the prompt echo → silent |
| `assistant` | `message`/`tool_call` per `message.content[]` block | `text`→message, `tool_use`→tool_call, `thinking`→silent; never `done`/`error` |
| `stream_event` | `message` (verbatim) | only emitted with `--include-partial-messages` (we never pass it); falls to the unknown-type default |
| `result` (success, `is_error:false`) | `message` (final text) | terminal `done` comes from exit code 0 (RunHandle); empty `result` → silent |
| `result` (`is_error:true` **or** error subtype) | `error` | **`is_error` is the discriminator, not the subtype** — the no-auth failure keeps `subtype:"success"`; text = `result`, else joined `errors[]`, else `terminal_reason` |
| non-JSON / unknown | `message` (verbatim) | defensive: never crash |

> **claude auth failure (observed):** with no auth configured, `claude -p` exits 1 and emits a final `result` line with **`subtype:"success"` + `is_error:true`**, `terminal_reason:"api_error"` and `"result":"Not logged in · Please run /login"` on stdout (nothing on stderr). The adapter maps it to a clear `error` event via the `is_error` flag.

## 6. Known adapter quirks (document in code + README)

- **opencode:** resuming keeps the session's original model unless `--model` is passed on resume (supported). The adapter's `resume(…, model=…)` appends `--model` when set, so the follow-up Model dropdown is honored. `--fork` can fork instead of continuing. `OPENCODE_DISABLE_AUTOUPDATE=1` is set on spawn.
- **opencode (spawn quirk, observed):** `opencode run --session <id>` **stalls with an empty stream when exec'd directly** by `subprocess.Popen` (the agent loop exits immediately after step 1), but runs correctly when spawned through a shell. The adapter therefore wraps every command in `/bin/bash -c 'cd <worktree> && exec opencode …'` (arguments are `shlex`-quoted). `--dir` starts are unaffected by the direct-spawn bug but use the same wrapper for consistency.
- **opencode (resume directory mismatch, observed Aug 2026):** headless `opencode run --session <id>` **hangs forever when resumed from a different worktree than the one the session was created in** — the model stream comes back empty, opencode logs `exiting loop`, and the process never exits. `resume` therefore passes `--dir <cwd>` (parity with `start`) **and** Jalebi always resumes from the session's own worktree: pr_review sessions live in the review worktree (`ws/task-<id>-review`, detached at the PR head), so `_run_followup` runs pr_review follow-ups there rather than in the task worktree. This is a hard requirement, not a nicety — resuming from the wrong worktree silently produces a run that stays `running` with an empty timeline (see `docs/06-task-queue.md` §4 for the stall guard that bounds it anyway).
- **codex:** `-m/--model` is **honored on resume** in 0.147.0 (source-verified: a resume without `-m` keeps the original session model; with `-m` it switches). `exec resume` has **no `-C/--cd`** — the cwd is wherever you launch it from, so the adapter uses the same `/bin/bash -c 'cd <worktree> && exec …'` shell wrapper as opencode. `exec` requires a git repo (or `--skip-git-repo-check`). **Sandbox:** applied via `-c` config overrides so it works uniformly on `exec` AND `exec resume` (resume has no `-s` flag). A cached `bwrap` probe decides the mode: sandbox usable → `sandbox_mode=workspace-write` + `sandbox_workspace_write.network_access=true` (network is off by default there); userns blocked (this dev machine: `bwrap: setting up uid map: Permission denied`) → `sandbox_mode=danger-full-access`, since `workspace-write` cannot write without user namespaces. The fallback is logged once with the host-level fix (`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` or setuid bwrap). **Auth:** reuses `~/.codex/auth.json` automatically; `CODEX_API_KEY` works for `exec` only; `OPENAI_API_KEY` is **not** read at runtime (only via `codex login --with-api-key`). **Models:** no `codex models` command — `list_models` reads `~/.codex/models_cache.json` (per-account slugs, honors `$CODEX_HOME`; API-callable models preferred via `supported_in_api`, with a curated fallback), overridable via the `adapter_model_lists` setting.
- **claude:** `--resume <id>` requires the session id captured from the first run (the `system/init` `session_id`). `--continue` resumes the last session only (do not rely on it). `--fork-session` (with `--resume`) creates a **new** session id. `--model` is honored on resume. `--output-format stream-json` **requires `--verbose`**. The adapter passes **`--permission-mode bypassPermissions`** (parity with opencode's bash `"*": "allow"`): `default` hangs on permission prompts and `dontAsk` auto-denies everything; deny rules from a worktree `.claude/settings.json` still apply in every mode. **Disk confinement:** the worktree guard adds a **`PreToolUse` hook** (`.claude/hooks/jalebi_deny_external.py`) that denies the file tools (`Read`/`Write`/`Edit`/`Glob`/`Grep`) outside the worktree — the claude equivalent of opencode's `external_directory: deny` — plus `Read`/`Edit` deny rules for sensitive home paths. Bash subprocess file I/O is NOT confined (same limitation as opencode). Auth precedence: `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` → OAuth; no auth → exit 1 with `"result":"Not logged in · Please run /login"` (`subtype:"success"` + `is_error:true`). **Claude reads `CLAUDE.md`, not `AGENTS.md`** — a claude-run worktree must also carry the Jalebi rules in `CLAUDE.md` (see §7). **Models:** no `claude models` command — `list_models` returns the curated alias list (`CLAUDE_CURATED`; `fable` was removed — not a real alias), overridable via the `adapter_model_lists` setting.
- Processes must be spawned with a **working directory = the task worktree** so the CLI discovers `AGENTS.md`/skills.
- Stream output parsing is **defensive & line-buffered**: iterate child `stdout` line-by-line (`text=True, bufsize=1`); unknown/non-parseable lines are shown verbatim in the console rather than crashing. `stderr` is drained in a background thread (bounded tail) to avoid pipe deadlock and to report exit failures.

## 7. Personality & skills injection (PRD §F6)

- Each catalog agent's personality and skills are stored in the DB; at run time the orchestrator:
  1. writes/merges the personality into the **task worktree's `AGENTS.md`** (root level, so the CLI auto-discovers it; claude also gets `CLAUDE.md`), and
  2. materializes the skills into **all three skill roots** of the worktree — `.claude/skills/`, `.codex/skills/` and `.agents/skills/` (the shared Agent Skills format) — and references them from `AGENTS.md` per backend: opencode/claude get the `@.claude/skills/<name>/SKILL.md` path; codex gets the `$<name>` trigger + the `.codex/skills/<name>/SKILL.md` path.
- Why three roots: opencode and Claude Code discover `.claude/skills` natively; codex does **not** resolve `@path` imports in `AGENTS.md` (it concatenates the file verbatim) and discovers skills from `.codex/skills` (legacy) and `.agents/skills` (current). Writing every root also keeps skills available when a follow-up forks the task onto another backend. All three roots are git-excluded (`INFO_EXCLUDE_LINES`) and the pre-commit hook rejects staging them.
- The **default agent of the CLI** (e.g. opencode's build agent) reads `AGENTS.md` + skills and uses them opportunistically. No custom agent definitions, no special prompts plumbing. For opencode this also works with its `.claude/skills`-compatible loading (`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` must remain unset). Because the mechanism is *files in the worktree*, it works for every backend.

## 8. Model selection (PRD §F5)

- No hard-coded model. Each action form exposes a **Model dropdown populated from the backend selected in that form** (`GET /api/models?cli=<backend>`; `adapter_model_lists` overrides the list per backend).
- The global Settings `default_model` is the fallback for an action that doesn't pick one — applied **only when the resolved backend equals `default_backend`** (a single model can't be valid for every backend); other backends use the CLI's own default when unpinned.
- A catalog agent may pin a model (task override > agent pin > default).
- UI shows the model used per task/run; follow-ups reuse the run's model by default but allow override (see §6 quirks).

## 9. Agent subprocess environment

- `start`/`resume` accept an `env` dict; `None` values **remove** the key from the inherited environ (used to strip `GH_TOKEN`/`GITHUB_TOKEN`/a leaked `JALEBI_GITHUB_TOKEN`).
- The queue builds the env via `_build_agent_env(token)`: `JALEBI_GITHUB_TOKEN` = the **selected account's** PAT for all task types (GitHub API use), but **no git push credentials** (`auth_env` is never applied — Jalebi is the only pusher). `GIT_AUTHOR_*`/`GIT_COMMITTER_*` = `Jalebi <jalebi@localhost>` always, plus the `gh`-neutralization vars. Inherited `GIT_CONFIG_*`/`GIT_DIR` state is stripped and `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` are pinned, so the parent shell's git config cannot hijack or redirect the agent's git.
- `_spawn` builds the subprocess env **from the passed env dict** (not `os.environ.copy()` + overlay), so a key the queue deliberately removed can never leak back in from the server's environment.
- `resume` now forwards `env` too (previously dropped it) — follow-ups get the same credentials/guards as the original run.
- **`ANTHROPIC_*` is deliberately passed through** (claude auth: `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` → OAuth; the CLI reads `~/.claude.json` too). Jalebi never sets or strips it — the owner's own claude configuration authenticates claude runs, exactly as codex reuses `~/.codex/auth.json`. `GH_*`/`GIT_*`/`JALEBI_GITHUB_TOKEN` stripping is unchanged and applies to every adapter.

## 10. New backends (Sep 2026)

Validated live Sep 2026 (one trivial prompt each, free-tier models) + local
`--help`. Resume follow-ups and tool-call/diff event shapes are per-docs and
structurally confirmed unless noted; live resume was exercised only where
stated.

- **pi** (`pi --print --mode json --approve`, 0.80.2): resume via
  `--session <id>` (`--resume` is a TUI picker — never use headlessly).
  `--model provider/id`; `openrouter/free` is a re-resolving pattern, not a
  fixed model. **Model failures exit 0** — `stopReason:"error"` on the
  terminal event is the error signal. Deltas (`message_update`) are silent;
  `message_end` is authoritative; `thinking` silent. No diff event.
  `list_models` parses `pi --list-models` (provider/model table).
- **kilo** (`kilo run --auto --format json`, 7.5.16): OpenCode fork — event
  model mirrors opencode. `-m` needs the full `provider/model` id
  (e.g. `kilo/kilo-auto/free`; bare `Auto Free` is rejected). Successful runs
  end on `step_finish` (`reason:"stop"`) + exit 0; non-stop reasons map to
  `error`. `file`-patch events map to `diff`. `list_models` reads
  `kilo models`. Resume (`-s`/`--continue`) and tool-call shapes are
  per-docs, not live-exercised.
- **qwen** (`qwen -p … -o stream-json --yolo`, 0.23.1): handshake is
  `system`/`init` (resume key `session_id`); terminal `result` line
  (`is_error` discriminator, final text echoed). `-p` deprecated but works.
  `--yolo` stderr warning silenced via `QWEN_CODE_SUPPRESS_YOLO_WARNING=1`.
  `--max-wall-time 600s` bounds headless runs. `list_models` reads
  `~/.qwen/settings.json` `modelProviders` ids (no live catalog call).
  Resume (`-r`/`-c`), tool events and partial streaming are per-docs, not
  live-exercised.
- **cline** (`cline --json --yolo`, 3.0.61): wire schema is
  `agent_event`/`run_result` (older `say`/`ask` docs are stale). `--yolo` is
  a hidden `--auto-approve` alias; `-t` is seconds. `-m` needs the full
  `modelType/model` id. No list-models command — curated free-tier ids
  (same precedent as claude). **Session id is not in stdout** — the resume
  key lives in `cline history --json`; streaming cannot capture it (follow-up
  falls back to a fresh session until history lookup is added). Resume
  (`--id`) and diff shapes per-docs, not live-exercised.
- **goose** (`goose run -t … --output-format stream-json`, 1.49.0): terminal
  event is `complete` (+ exit 0); CLI errors go to stderr with exit 1.
  Stdout starts with a non-JSON banner (blank lines dropped, rest verbatim).
  Sessions are **named** (`-n`) in one global SQLite DB — the adapter derives
  `jalebi-<worktree>` per worktree so same-name resumes can't cross tasks.
  Resume (`-r -n`) and tool/diff shapes per binary vocabulary, not
  live-exercised. No list-models command (`list_models` → `[]`).
