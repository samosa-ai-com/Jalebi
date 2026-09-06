# Jalebi — Product Requirements Document

|                          |                                                                                                                                                 |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Product name**   | Jalebi                                                                                                                                          |
| **Status**         | Draft v1.2 — reconciled with the shipped implementation (Phases 0–2 complete)                                                                    |
| **Date**           | 2026-08-09                                                                                                                                      |
| **Owner**          | Samosa AI (`samosa-ai-com`)                                                                                                                     |
| **Repo**           | `Rishabh-Bajpai/Jalebi` (current home). This PRD lives in the Jalebi repo.                                                                       |
| **Versioning**     | Follow this file; feature set is additive                                                                                                       |

> **Reading note (2026-08-09):** this document was reconciled against the shipped
> implementation after Phases 0–1 (Draft v1.1) and Phase 2 (Draft v1.2). It is the
> **spec of record** for what exists and what is planned. Where the earlier draft
> specified a Node/TypeScript stack, this version adopts the actual Python/Flask
> stack. §F15 was owner-authorized to specify **commit statuses** (the check-runs
> API is GitHub-App-only and PATs cannot write it). Sections marked **Phase 3**
> describe planned work that is not yet implemented.

---

## 1. Executive summary

Jalebi is a **private, self-hosted, localhost-only web application** that behaves like Google's "Jules (https://jules.google/docs/)": a coding-agent dashboard where the repo owner connects their own GitHub account, creates tasks ("fix this issue", "review this PR", "implement this feature", "audit the security posture of the development branch"), watches the agent work through a live step-by-step timeline, comments to give follow-ups, and gets proactively notified of improvement opportunities. It is also **event-driven**: repo webhooks can auto-start work in real time — the moment a PR is opened, its assigned reviewers from the catalog begin reviewing automatically.

**Current status (2026-08-09):** Phases 0, 1, and 2 are **complete and shipped**. The backend is the **opencode** CLI adapter only; Codex and Claude Code are planned (Phase 3). See §12 Roadmap.

Unlike the existing **Chanakya** automation in the Gotcha repo (GitHub Actions + self-hosted runner + comment-triggered `opencode github run`), Jalebi is **completely independent**:

- No `.github/workflows`, no self-hosted runner, no comment-triggered slash commands. Event automation uses **repo webhooks** delivered to a local listener.
- Uses the owner's **GitHub personal access token(s) (PAT)** directly, from a **named vault** of equal accounts (see §F1).
- Runs agents as **local child processes** on the user's machine.
- The **UI is a dedicated custom dashboard**, not a comment thread.
- **Backend CLI-agnostic**: the same app can drive opencode, Codex, or Claude Code. In v1 only **opencode** ships; switching the backend is intended to be a **one-line config change** (`agent.cli`).

The product name is **Jalebi** (a coiled Indian sweet — evoking a queue of work being processed).

---

## 2. Background & motivation

### 2.1 Current setup: Gotcha + Chanakya (existing automation — context for the new developer)

The Gotcha repo currently automates AI coding work via a GitHub Actions workflow. A developer building Jalebi should understand it, because Jalebi replaces this interaction model entirely while reusing the same underlying agent technology.

**Repo:** `samosa-ai-com/Gotcha`
**Workflow file:** `.github/workflows/chanakya.yml`

**Trigger model (comment-driven):**

- A GitHub user posts a comment starting with `/chanakya <command>` on an issue or PR.
- The workflow receives the `issue_comment` / `pull_request` webhook event and hands it to `opencode github run` (the official opencode GitHub agent handler), running on a **self-hosted runner**.
- The agent responds by posting a comment on the same issue/PR (with a footer containing the share/GitHub-run links) and reacts with an 👀 emoji while working, removing it when done.

**Workflow jobs (all `runs-on: [self-hosted, chanakya]`):**

| Job              | Trigger                          | Purpose                                                                          |
| ---------------- | -------------------------------- | -------------------------------------------------------------------------------- |
| `review-pr`    | `/chanakya review` on a PR     | Reviews the PR diff, posts a comment                                             |
| `review-issue` | `/chanakya review` on an issue | Analyzes the issue, proposes/explains a fix, posts a comment                     |
| `triage`       | `/chanakya triage`             | Classifies an issue (severity/type), posts a comment                             |
| `fix`          | `/chanakya fix`                | Clones the repo, creates a branch, fixes the issue, opens a PR with `Closes #N` |
| `general`      | `/chanakya <anything else>`    | Free-form Q&A about the repo, posts a comment                                    |

**Key workflow facts a developer must know:**

- Each job sets env vars consumed by `opencode github run`: `TOKEN` (GitHub token), `EVENT` (the webhook event JSON), `SHARE` (`"false"`), `USE_GITHUB_TOKEN` (`"true"`), `MODEL`, `PROMPT` (the `/chanakya` command mapped to an English instruction), and **the trigger comment body is prepended to `PROMPT`** (this was a bug fix — without it, free-text instructions after the command were ignored).
- Command routing uses **strict `startsWith(comment.body, '/chanakya <cmd>')`** checks (not `contains`), and `github.event.comment.user.type != 'Bot'` excludes bot comments (both intentional hardening).
- `fix` job: the opencode handler itself creates a branch, runs the agent, commits, pushes, and opens the PR (`Closes #N`). Note: with `USE_GITHUB_TOKEN: "true"` the handler **skips `configureGit()`** (git credential/user config), so on a fresh self-hosted runner the `git push` may fail — this is a known caveat; the `fix` job has historically ended `skipped`/`failure`.
- There is **one self-hosted runner** (`actions-runner-samosa`, label `chanakya`). Jobs therefore execute **sequentially (one at a time)** even when several issues are triggered simultaneously; queued jobs wait for the running job to finish.
- No `concurrency:` block is set on the workflow.

**Limitations that motivate Jalebi:**

1. Work is only initiated by comments on GitHub; there is no dashboard, no scheduling, no proactive scanning, no event-driven automation (nothing auto-triggers when a PR opens).
2. Parallelism is capped by the single runner.
3. No concept of per-agent identity, personality, skills, or model choice — every task uses the same opencode build agent and one configured model.
4. No way to select source/target branches per task.
5. Work is public-ish (any collaborator who knows the command can trigger it) and entangled with GitHub's comment UX.

### 2.2 The Jalebi model

Jalebi inverts the control flow:

```
Chanakya:  GitHub comment ──▶ GitHub Actions ──▶ self-hosted runner ──▶ opencode github run ──▶ comment back
Jalebi:    local web UI / GitHub webhook event ──▶ Jalebi orchestrator ──▶ (spawn CLI agent as child process) ──▶ push/PR/commit-status via PAT ──▶ UI updates
```

Everything is local, private to the owner, and driven from the dashboard.

---

## 3. Goals

1. **Jules-like experience, self-hosted.** A dedicated web UI with a task queue, a per-task step-by-step timeline, live streaming logs, an incremental diff view, and a follow-up/comments panel. Owner-only; no comment triggers.
2. **Pluggable agent backends.** v1 ships the **opencode** CLI. Architecture must support adding **Codex** and **Claude Code** (and others) with a **one-line config change** (`agent.cli`) and no changes elsewhere.
3. **Model freedom.** The user can pick any model available to the selected CLI, per task and per agent (no hard-coded model).
4. **Agent catalog with personalities & skills.** Users configure named agents (e.g. "Security Auditor", "Backend Reviewer", "Docs Guru", "Conflict Resolver"). Each is just: a **personality** (markdown injected into the task worktree's `AGENTS.md`) + a set of **skill files** (markdown, referenced by path) + an optional **model** + an optional **CLI**. The underlying default agent (opencode build agent) picks these up automatically during execution.
5. **Reviewer workflow.** Users assign catalog agents as reviewers on a PR. Each reviewer works in its **own local worktree**, validates, then posts its comments on the GitHub PR. The user then manually approves/merges, and can send a follow-up to the original fix agent referencing the reviewers' comments.
6. **Branch control.** Branch selection is **per task type** (as shipped): `issue_fix` uses a **single target branch** (the PR base); `freeform` exposes **source and target** selectors; `pr_review` uses neither (it checks out the PR head).
7. **Proactive screening.** Scheduled, multi-profile audits ("suggestions for improvements") with **per-profile cadence and system prompt**. Screening is **notify-only — it never auto-acts** on issues; the user explicitly chooses to start work. *(Phase 2.)*
8. **Privacy & ownership.** Localhost-bound, PAT-authenticated, single-owner. The owner can register **multiple named PAT accounts** (a vault of equal accounts) and select which account runs a given repo/task.
9. **Event-driven automation (essential).** Repo webhook events can auto-start tasks in real time — e.g. the moment a PR is opened, its assigned reviewers from the catalog begin reviewing automatically. Triggers are **webhook-pushed**, not polled (scheduling is not the mechanism; triggering is).
10. **Simplicity above all.** The implementation must stay **logically simple**. Do not import whole subsystems or frameworks from reference projects just because they exist — borrow only the specific ideas/snippets that directly serve a feature, and prefer the simplest code that satisfies the PRD. Avoid over-engineering (no event-bus frameworks, no complex state machines, no distributed abstractions) unless a requirement literally demands it. If a feature starts feeling complex to implement, stop and revisit the design.
11. **Reliability by default.** Runs survive restarts and are **auto-recovered** on failure/timeout/stall (bounded by `retry_policy.max_attempts`, per-run escalating timeouts, first-failure + give-up notifications). A run that goes quiet (stall) is detected by a watchdog and restarted. See §7.5 and §F16.

## 4. Non-goals (v1)

- **No** slash-command triggers from GitHub comments (no `/chanakya`-style `issue_comment` listener). Event automation is **webhook-driven** (repo events), not comment-driven.
- **No** multi-user accounts, roles, or team collaboration.
- **No** hosting in the cloud; localhost only.
- **No** GitHub App / OAuth app — PAT only in v1 (a GitHub App can be a future option).
- **No** auto-acting screening (screenings never open issues/PRs or start fixes on their own).
- **No** shipping the Gemini CLI adapter (explicitly replaced by **Codex**).
- **No** polling as the triggering mechanism — webhooks are the default and preferred path. A per-repo **polling fallback** toggle exists in the schema/UI but is currently **inert** (deferred).
- **No** replacing Chanakya itself — Chanakya remains in Gotcha; Jalebi is a separate, independent project.

---

## 5. Personas

- **The owner (primary):** owns the repos, connects one or more named PAT accounts, creates tasks, approves PRs, configures agents and screenings. Only persona.
- **The downstream developer:** reads the PRD and builds Jalebi on any machine. This document is their single source of truth for behavior + context.

---

## 6. Glossary

| Term                       | Meaning                                                                                |
| -------------------------- | -------------------------------------------------------------------------------------- |
| **Task**             | A unit of work in Jalebi (fix issue, review PR, free-text instruction, screen).        |
| **Run**              | One agent execution (one CLI child process) within a task.                             |
| **Follow-up**        | A user comment on a task that**resumes** the same agent session.                 |
| **Recovery run**     | An **auto**-dispatched rerun on failure/timeout/stall — resumes the session or starts fresh; tagged `auto`, never recorded as a user follow-up. |
| **Stall**           | A run that stops emitting output for longer than `stall_timeout_seconds` (default 600); the watchdog declares it stalled and auto-recovery restarts it fresh. |
| **Adapter**          | A backend integration that maps a CLI (opencode/Codex/Claude) to one common interface. |
| **Named account**   | One of the owner's registered GitHub PATs in the vault — all **equal** (no primary/fallback); the account selected for a task/repo is the one used. |
| **Catalog agent**    | A user-configured named agent = personality + skills + optional model + optional CLI.  |
| **Reviewer**         | A catalog agent of kind`reviewer` assigned to a PR.                                  |
| **Screen/Screening** | A scheduled proactive audit with its own system prompt + cadence.                      |
| **Webhook trigger**  | A repo webhook event (e.g. `pull_request.opened`) that auto-starts task(s) per user rules. |
| **Commit status**    | A GitHub commit status reporting a task's pending/success/failure/error state — can gate merges via branch protection. *(Phase 2.)* |
| **Artifact**         | A file produced by a run (log, report, coverage) captured and retained per run.        |
| **Worktree**         | A git worktree — an isolated checkout of a repo for a single task/agent.              |
| **Publish**          | Push branch + open (or update) a PR.                                                   |
| **Publish mode**     | `new_pr` (open/reuse a PR), `update_pr` (force-push into an existing PR's head), `push_branch` (push to a named branch). |
| **Resume**           | Continue an existing agent session (CLI-native continuation).                          |

---

## 7. High-level product behavior

### 7.1 Core loop (single task)

1. User connects one or more named PAT accounts (Settings).
2. User creates a task: pick a repo, choose a task type, select branches **per the type's rule** (§F8), pick a **catalog agent** (or the default build agent), optionally choose a **model**, and enter instructions (issue number / PR number / free text).
3. Orchestrator creates a **worktree**, writes the personality/skills files, and runs the agent via the selected CLI adapter (`start`).
4. UI streams the agent's **step timeline** (scanning → planning → implementing → testing → creating PR), the **live console**, and the **incremental diff**.
5. On completion, Jalebi **publishes** per the publish policy (default: auto-open PR) and shows the PR link. If the run **fails, times out, or stalls**, auto-recovery (§7.5) takes over instead of leaving the task dead.
6. User can post a **follow-up** → the orchestrator **resumes** the same agent session in the same worktree → agent amends its work → branch/PR updated.
7. User approves/merges the PR manually on GitHub.

### 7.2 Reviewer loop

1. A PR exists (created by a fix task, or an existing external PR).
2. User assigns 1..N reviewers from the catalog.
3. Each reviewer runs as its **own `pr_review` task in its own review worktree**, checks out the PR branch, reviews with its own personality/skills/model/CLI, validates (builds/tests), then **posts a PR review comment** via the GitHub API.
4. When all reviewers have commented, the user decides (approve, request changes, merge) — **manually**.
5. Optionally, the user sends a **follow-up** to the original fix agent: "Look at the reviewers' comments and fix accordingly." The fix agent resumes, sees the PR comments (fetched from GitHub), and updates the PR.

### 7.3 Screening loop *(Phase 2)*

1. User enables/creates screenings per repo (each with its own system prompt + cron cadence).
2. Scheduler checks cadence; skips if the repo HEAD is unchanged since the last run (baseline dedup).
3. Runs a read-only audit session; parses findings; stores them; **notifies** the user (in-app + optional ntfy push).
4. Findings are browseable; the user can convert a finding into a task — **never auto-started**.

### 7.4 Event-triggered loop (webhook-driven)

1. GitHub delivers a **repo webhook event** to Jalebi's local listener (e.g. `pull_request` opened, `issues` opened, PR updated).
2. The listener validates and **idempotently** dedups the delivery (`X-GitHub-Delivery` / `X-GitHub-Event` headers), then matches it against the user's **trigger rules**.
3. A matching rule creates and enqueues task(s) immediately — e.g. a PR just opened ⇒ each **assigned reviewer** gets its own review task and starts in real time.
4. Runs proceed exactly like a manual task (timeline, logs, diffs) and report back via **commit statuses** on the PR's head commit when configured *(Phase 2)*.
5. No polling involved; the trigger is the event itself. (A manual "replay last delivery" button exists; an optional polling fallback is inert.)

### 7.5 Recovery loop (auto-recovery)

1. A run ends **terminal-failed** (including **stalled**) or **timed-out**.
2. Auto-recovery (default ON, bounded for all task types) dispatches a new run:
   - **timeout / other failure** → **resume the last session** with the `continue_prompt` (`retry_policy.continue_prompt`, default `"continue"`);
   - **stall** → **fresh re-run** (a wedged session re-hangs — a stalled session is never resumed);
   - no resumable session → fresh run.
3. The per-run timeout **escalates**: `timeout_minutes × timeout_multiplier^attempts`, capped at `retry_policy.max_timeout_minutes` (default 180). `task.timeout_minutes` is never mutated.
4. A `done` run **resets `retry_count`**; a manual rerun after success starts from the base timeout again.
5. Recovery runs are tagged `auto` (queue + run) and are **never recorded as user follow-ups**; the owner gets a notification on the first failure and on the final give-up/success — intermediate attempts are timeline-only (see §F19).
6. The loop is **bounded by `retry_policy.max_attempts`** (default 3): at the cap the task stays `failed` with a give-up note instead of requeueing. Failures matching a `retry_policy.non_retryable_patterns` phrase (wrong model, bad auth, missing token/session) fail immediately with no recovery — a deterministic failure must never loop until the owner cancels it.

---

## 8. Detailed features

### F1. GitHub integration (named PAT vault)

- The owner registers **one or more named PATs** in Settings. Every token is stored by name in the `0600` secrets file `<data-dir>/secrets.json` (`github_tokens: [{name, token}]`).
- **All accounts are equal** — there is **no primary/default account and no fallback**. The account selected for a task/repo is the one used; a task without an account is refused.
- A fine-grained or classic PAT per account. Required scopes (document in UI): classic `repo` (or fine-grained: Contents read/write, Pull requests read/write, Issues read/write, Metadata read, **Commit statuses read/write** for commit statuses).
- **`JALEBI_GITHUB_TOKEN`** is **masking-only** (a stray value never survives into logs); it is never used to resolve which account runs anything. While developing/testing it may also be set in the git-ignored `.env` file.
- Tokens are stored on disk (never in the browser; the backend proxies all GitHub calls). All GitHub calls go through a thin **httpx** client (`github.py`).
- Capabilities used: repo list & default branch, issues, PRs (create/update/comment/review), refs, clone/push via authenticated git, **repo webhook registration/management**, **commit statuses** *(Phase 2)*.
- **Webhook setup:** Jalebi registers repo webhooks via the API (`POST /repos/{owner}/{repo}/hooks`) targeting its local listener (URL + optional secret for signature verification). For a localhost-only install, GitHub cannot reach the machine — the listener must be exposed via a **tunnel (e.g. `cloudflared`/`ngrok`)** or the webhook URL points at a small reverse proxy; the app detects an unreachable webhook and warns, offering the (inert) polling fallback.
- **Safety:** token values are never exposed to the frontend or to agent prompts; all PAT values are masked from logs/console at ingest (see §F17).

### F2. Local web app / UI (Jules-like)

Layout (three areas):

1. **Left rail — Task queue:** list of tasks with status (`queued / running / waiting-review / needs-approval / done / failed / timed-out / interrupted / cancelled`), repo, agent, model, PR link. Filterable.
2. **Main panel — Task detail:**
   - **Step timeline:** ordered phases of the current run (scanning → planning → implementing → testing → creating PR → reviewing) with timestamps; the agent adapter emits `step` events.
   - **Live console:** streaming stdout of the CLI child process (masked at ingest).
   - **Diff viewer:** incremental file diffs as they appear; today this is the captured `runs.diff_text` (per-file expand/polish is Phase 2).
   - **Follow-up composer:** free-text comment → resumes the session.
   - **PR card:** status of publish, link to the GitHub PR, reviewers assigned + reviewer comment status, publish modes (`new_pr`/`update_pr`/`push_branch`), **commit-status** *(Phase 2)*.
3. **Top bar — Global nav:** Repos · Tasks · Agents · Triggers · Settings. (*Screenings* lands with Phase 2.)

Non-goal: build nothing on `opencode web`'s chat UI — Jalebi's UI is bespoke and orchestrator-centric (queue + timeline + diffs + reviewers), not a direct agent chat.

### F3. Task queue & concurrency

- **Default concurrency: 4** parallel tasks, **configurable from the UI** (0 = paused queue). Read live per run (settings are persistent rows, not process-time constants).
- Worker pool executes the configured max number of tasks concurrently; the rest wait in queue.
- Cancel/abort a running task (kill child process: SIGTERM → SIGKILL after a grace period).
- Tasks survive restarts (persisted in SQLite; interrupted runs are marked `interrupted` and resumable).

### F4. Agent adapters (backend CLI independence)

**Principle (P0):** the entire system depends on one interface; the only place that knows the CLI name is the adapter and a one-line config.

The shipped interface is a Python protocol (`adapters/types.py`):

```python
class AgentAdapter(Protocol):
    id: str                              # "opencode" | "codex" | "claude"
    name: str
    def list_models(self) -> list[str]: ...
    def start(self, *, cwd, prompt, model=None, env=None) -> RunHandle: ...
    def resume(self, *, cwd, session_id, prompt, env=None) -> RunHandle: ...
    def parse(self, line: str) -> list[AgentEvent]: ...
```

`RunHandle` exposes `session_id`, the child process handle, and an async iterator of `AgentEvent`s. (Phase 3 may add `--output-schema` support for structured screening findings.)

`AgentEvent` normalized vocabulary:

| Event                | Meaning                                                                         |
| -------------------- | ------------------------------------------------------------------------------- |
| `step`             | Phase transition (scanning/planning/implementing/testing/creating-pr/reviewing) |
| `tool_call`        | Agent invoked a tool (file edit, bash, grep, …)                                |
| `message`          | Agent text output                                                               |
| `diff`             | File-change delta (used for the diff viewer)                                    |
| `done` / `error` | Terminal states                                                                 |

**Backend selection:** `agent.cli` in config/Settings ("opencode" | "codex" | "claude"). One line. Everything else — queue, git, GitHub, UI, screenings, catalog — is untouched by a backend change.

Adapter command references (authoritative, captured 2026-08):

| CLI                      | Start a new task                                                             | Resume (follow-up)                                                         | Structured output                                                                                                     | Model flag                                   |
| ------------------------ | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **opencode** (v1, shipped) | `opencode run --dir <ws> --format json [--model <m>] <prompt>`             | `opencode run --session <sessionId> --format json <prompt>`              | `--format json` (event stream; contains `session.id`, `session.updated`, `message.updated`, `session.idle`) | `-m/--model provider/model`; env `MODEL` |
| **codex** (Phase 3)  | `codex exec --json [--model <m>] "<prompt>"`                               | `codex exec resume <session_id> "<prompt>"`                              | `--json`; `--output-schema` for structured findings                                                               | `-m/--model` (or `config.toml`)          |
| **claude** (Phase 3) | `claude -p "<prompt>" --output-format stream-json --verbose [--model <m>]` | `claude -p "<prompt>" --resume <session_id> --output-format stream-json` | `--output-format stream-json` (`init.session_id` + typed events)                                                  | `--model`                                  |

**Known adapter quirks (must be documented in code + README):**

- **opencode:** resuming keeps the session's original model unless `--model` is passed on resume (supported). `--fork` can fork instead of continuing if the user prefers a clean follow-up.
- **codex:** **on resume, the model/reasoning-effort cannot be changed** — the resumed session retains the original run's settings. Model changes on follow-ups must start a fresh run or be surfaced in the UI (open Phase 3 decision).
- **claude:** `--resume <id>` requires the session id captured from the first run (`init.session_id`). `--continue` resumes the last session only (do not rely on it).
- Processes must be spawned with a **working directory = the task worktree** so the CLI discovers `AGENTS.md`/skills.
- Parsing must be **defensive**: unknown/non-parseable lines are shown verbatim in the console rather than crashing.

### F5. Model selection

- **No hard-coded model.** Each task exposes a model dropdown populated from the active adapter's `listModels()` (shipped for opencode via `/api/models`).
- Per-task default: the adapter's configured default (e.g. opencode config default, or `config.toml` default for codex).
- A **catalog agent** may pin a model (e.g. a reviewer pinned to a different model than the fixer → intentionally different review output).
- UI shows the model used for each task/run; follow-ups reuse the run's model by default but allow override where the CLI permits it (see F4 quirks).

### F6. Agent catalog (personality + skills)

Deliberately **simple** — a catalog agent is not a new agent type. It is only a personality file + skill files + optional model + optional CLI.

**Catalog agent definition (stored in Jalebi, editable in UI):**

```yaml
id: security-auditor
name: Security Auditor
kind: general | reviewer        # reviewers get the reviewer workflow
cli: opencode                   # optional backend override
model: openai/gpt-5.1           # optional pin
description: Finds vulns & bad security practices
personality: |                 # markdown → merged into AGENTS.md
  You are a senior application security engineer. Be adversarial...
skills:
  - secure-coding.md
  - owasp-top10.md
custom_instructions: |         # appended to the task prompt when this agent is selected
  Pay extra attention to auth, secrets, and injection.
```

**Personality → `AGENTS.md` injection (the core mechanism):**

1. Each catalog agent's files live in a dedicated directory on disk, e.g. `<jalebi-data>/agents/<agentId>/` containing `personality.md` (or `AGENTS.md` fragment) and `skills/*.md`.
2. When a task uses a catalog agent, the orchestrator:
   - writes/merges the personality into the **task worktree's `AGENTS.md`** (root level, so the CLI auto-discovers it), and
   - materializes the skill files into the worktree as `.claude/skills/<name>/SKILL.md` and **references them from `AGENTS.md` via `@path` links** (stale skills are pruned when the list changes), plus passes the **directory path** on the run command where the CLI supports it (e.g. `--dir`/cwd).
3. The **default agent of the CLI** (e.g. opencode's build agent) reads `AGENTS.md` + skills and uses them opportunistically during execution. No custom agent definitions, no special prompts plumbing.
4. For opencode specifically this also works with its `.claude/skills`-compatible loading (`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` must remain unset) and `AGENTS.md` auto-discovery.

This satisfies: *"They can be at certain location, and you may just inject the address of those in your request when you run the opencode run command. And the default agent of opencode, like the build agent, will automatically pick those personality and skill files if it feels like it needs during the execution."*

**Model/skill independence:** because the personality+skills mechanism is *files in the worktree*, it works identically for opencode, Codex (`AGENTS.md` is read by Codex too), and Claude (`CLAUDE.md`) — the adapter only varies the file/dir conventions.

### F7. Reviewer workflow

1. **Assigning:** on a task's PR card (or a standalone "Review PR" task), the user selects reviewers from the catalog (kind `reviewer`). Each reviewer runs as its **own `pr_review` task** in its **own review worktree**, under the queue's normal concurrency.
2. **Execution:** each reviewer checks out the PR's head branch and runs a review session with its own personality/skills/model/CLI (via the same adapter interface — `start`, not `resume`).
3. **Validation:** the reviewer is instructed (via a system prompt injected in its task prompt) to build/test what it touched if feasible, and to report blockers.
4. **Posting:** on completion, the orchestrator posts the reviewer's output as a **PR review comment** on GitHub (using its PAT; e.g. `POST /repos/{owner}/{repo}/pulls/{n}/reviews` with `event: "COMMENT"` and body = the review wrapped with the Jalebi header/CTA via `messaging.wrap_pr_review`). The UI tracks which reviewers have posted.
5. **Dedup:** `review_assignments` has `UNIQUE(repo_id, pr_number, agent_id)` — concurrent webhook deliveries/manual calls cannot double-assign a reviewer. On collision, the existing assignment's task is reused.
6. **Approval is manual:** Jalebi never approves/merges. When all reviewers have posted, the user approves or requests changes on GitHub directly.
7. **Follow-up to the fixer:** the user can send a follow-up to the original fix agent: the orchestrator **resumes** the fixer's session, and (via its prompt) instructs it to fetch the current PR review comments from GitHub and address them. The fixer amends the branch; the PR updates.

### F8. Branch selection (source/target)

Branch selection is **per task type** (shipped behavior; supersedes the earlier two-selector-everywhere text):

- **`issue_fix`** — **single target branch**: the worktree is based on the target (PR-base) branch and the PR opens into it. This was a deliberate owner decision (Step 32) to match how the existing automation works.
- **`freeform`** — exposes **source and target** selectors: worktree from **source**, PR `base = target`.
- **`pr_review`** — neither: checks out the PR head directly.

### F9. PR publishing policy

- **Default: auto-publish** — on task completion the orchestrator pushes the branch and opens a PR. Title = agent's `.jalebi/pr.md` first `# <title>` line (fallback: `🦦 Jalebi: <first prompt line>` / `🦦 Jalebi task`); body includes the agent-written `.jalebi/pr.md` body (or raw prompt as fallback) + `Closes #N` when an issue was referenced + the Jalebi brand footer (`🦦 Opened by [Jalebi](https://github.com/samosa-ai-com/jalebi) — your self-hosted AI coding agent by [Samosa AI](https://github.com/samosa-ai-com).`) + `Co-authored-by: Jalebi <jalebi@samosa-ai.com>`. External posts must not contain task IDs, `localhost`, internal URLs, or any other owner-only info — see `docs/14-messaging-strategy.md` for the templates and invariants.
- **Three publish modes** — manual publish (`POST /api/tasks/<id>/publish` body `{mode, branch?, pr_number?}`) supports three modes; auto-publish always uses `new_pr`:
  - **`new_pr`** *(default)* — push `jalebi/<id>` and open/reuse a PR into `task.target_branch`.
  - **`update_pr`** — fast-forward (or merge) `jalebi/<id>` into an existing PR's head branch and force-push with `--force-with-lease`. Requires `pr_number` (or `task.prs_json[0]`). No new PR opened; no `Closes #N` comment.
  - **`push_branch`** — fast-forward (or merge) `jalebi/<id>` into a named branch and force-push with `--force-with-lease`. Requires `branch`. No PR interaction.
  - On merge conflict (any mode): `PublishConflict` (409) with the conflicting file list. On remote-moved-since-fetch (`--force-with-lease` refusal): `PushLeaseFailed` (412). UI shows both as clear inline errors. `--force-with-lease` is the only force variant used — never `--force`, so concurrent pushes by others are protected.
- **Smart-default Publish button:** for `freeform` / `screen_finding` / `triggered` tasks with `task.prs_json` non-empty, the manual Publish button reads **"Push to PR #N"** and dispatches `update_pr` for the first linked PR (the common case — work done against an existing PR). Otherwise it reads **"Publish"** and dispatches `new_pr`. An **Advanced** disclosure exposes all three modes + a PR picker (when `prs_json` has >1) + a branch text input for `push_branch`. `issue_fix` always defaults to `new_pr` (its canonical purpose is opening a PR with `Closes #N`); the other modes are still available under Advanced.
- **Configurable:** per-task or global setting `auto_publish: true|false`; when `false`, the UI shows a **"Publish"** button (push + open PR) that the user clicks, and a "push-only" option. `issue_fix` defaults to `auto`; `freeform`/`screen_finding`/`triggered` default to `manual`.
- **PR updates on follow-ups:** follow-ups amend the same branch; existing PR is force-updated (new commit pushed) — never a second PR for the same task.

### F10. Proactive screening *(Phase 2)*

**Design principle: screening finds and notifies; it never acts.** No screening auto-opens issues, opens PRs, or starts fix tasks. The user converts findings into work.

- **Screen catalog** — each screen = `{ id, name, systemPrompt, cadence (cron), scope (repo/branch), enabled, notify }`.
- v1 ships with a starter catalog (user-editable):
  - *Security posture* — vulns, secrets, injection, authz gaps, dependency risk.
  - *Dependency hygiene* — outdated/abandoned/misconfigured dependencies.
  - *Dead code & cruft* — unused exports, dead branches, TODO/FIXME density.
  - *Test coverage gaps* — critical paths without tests.
  - *Docs drift* — README/AGENTS docs out of sync with code.
  - *Performance hotspots* — obvious N+1 / heavy loops / unbounded growth.
  - *Code-quality consistency* — style inconsistencies across modules.
- **Scheduler:** a cron scheduler per screen (planned: **APScheduler** or a simple timer — the Python equivalent of the originally-planned `node-cron`). **Baseline dedup:** store last audited `HEAD` per (repo × screen); skip if unchanged. Run the screen at HEAD with a **read-only prompt** (no edits, no git writes) and an optional structured-output schema for findings.
- **Findings model:** `{ severity, title, file, line?, detail, recommendation }`.
- **Notification:** in-app (screenings tab, unread badge) + optional **ntfy** push (a topic the user sets) — the owner's environment already uses ntfy.
- **UI:** Screenings tab to configure cadence, enable/disable, view history, and **"New task from finding"** (opens a prefilled `screen_finding` task — still requires the user's explicit action).

### F11. Follow-ups (resume)

- Any task with a completed run shows a **follow-up composer**.
- Posting a follow-up → orchestrator calls `adapter.resume({ sessionId, prompt: followup + context })` in the **same worktree**, same branch.
- For a reviewer follow-up or "address the reviewers" request, the prompt includes the current GitHub PR review comments (fetched via API) so the agent can see them.
- Follow-ups must be **backend-agnostic** (works for opencode, codex, claude).
- Session ids are persisted per run (`runs.session_id`) so follow-ups survive restarts.
- **Distinct from auto-recovery:** user follow-ups are recorded in `followups`; **auto-recovery** runs are tagged `auto` and are never recorded as follow-ups (see §7.5/§F16).

### F12. Storage

- **SQLite** via **SQLAlchemy 2 + Alembic** (schema managed by migrations; run at startup).
- Tables (shipped): `repos`, `tasks`, `runs`, `followups`, `artifacts`, `catalog_agents`, `review_assignments`, `trigger_rules`, `event_deliveries`, `env_vars`, `settings` (key-value, see §10 for the full key list). **Phase 2** adds `screenings`, `screening_runs`, `check_runs`.
- Data dir (default `~/.jalebi/`; `JALEBI_DATA_DIR` overrides): `data.db`, `secrets.json` (0600), `repos/` (bare mirrors), `ws/` (worktrees), `agents/` (catalog agent files), `logs/`.
- Prune policy: delete task worktrees for `done` tasks after a configurable TTL (default 7 days, `artifact_ttl_days`) unless a PR is still open.

### F13. Security & privacy

- Bind server to **127.0.0.1** by default. Optional UI password (env `JALEBI_PASSWORD` or `OPENCODE_SERVER_PASSWORD` reuse) gates the API + SPA behind Basic auth when tunnel-exposed.
- **Failed-login alerts:** a wrong password pushes a throttled (1/60 s per client) ntfy alert — masks any token-like username; the alert is best-effort and never delays the 401.
- Named PATs stored with `0600`; never logged, never sent to the browser, never passed to agent prompts. All PAT values are masked at ingest (see §F17).
- **Agent sandboxing / `gh` guard:** each child process is scoped to its own worktree (cwd). The per-worktree `opencode.json` **denies `gh`** via opencode permission rules and denies external-directory access; the agent env carries an empty `GH_CONFIG_DIR` and stripped `GH_TOKEN`/`GITHUB_TOKEN`, and the `gh` CLI is banned project-wide (§17.2). A settings toggle can add a sandbox wrapper (e.g. `bwrap`/`firejail`) later.
- Publishing is idempotent; follow-ups only ever touch the task's own branch.
- If the app is ever exposed (tunnel), require the UI password and document the risk. Webhook deliveries are HMAC-verified when a secret is configured; the `/webhook` listener is exempt from the Basic-auth gate (GitHub doesn't send credentials).

### F14. Event-driven triggers (webhooks)

Triggering is a first-class, **webhook-pushed** mechanism (not polling, not scheduling).

- **Listener:** a local HTTP endpoint (`POST /webhook`) receiving repo webhook events. Validated by optional `X-Hub-Signature-256` secret; **idempotent** — deliveries deduped on the **UNIQUE `X-GitHub-Delivery`** (the DB constraint is the atomic reservation that wins concurrent re-deliveries), so re-deliveries never double-run a task. Unconnected repos are ignored.
- **Trigger rules:** per repo, user-configurable rules of the form:
  - event: `pull_request.opened` | `pull_request.synchronize` | `pull_request.reopened` | `issues.opened` | `pull_request_review` | `push` (…)
  - action: `start_review` | `triage_issue` | `create_task` | `rerun_review`
  - scope: branch filter (optional), labels (optional), PR author (optional)
  - target: which catalog agent(s) / reviewer(s) to launch, with custom instructions.
- **Core use case:** `pull_request.opened` ⇒ auto-start the **assigned reviewers** from the catalog; each begins its own review task immediately. A `synchronize` (new push) can optionally re-trigger a reviewer pass.
- **Reviewer auto-assignment source:** trigger rules may reference a default reviewer set, or reviewers can be assigned per-PR via the UI; the webhook path uses whichever applies. Reviewers are deduped against already-assigned; a `rerun_review`/`start_review` whose assignments are all already made records a delivery `status="failed"` (work == []).
- **Delivery log & replay:** every delivery is recorded (`event_deliveries`) with its **result** (`result.rules[]`, per-rule `work`); the UI offers **replay** for any delivery. Replay is idempotent: rules whose stored work is non-empty are skipped (a rule added *after* the delivery still fires).
- **Semantics:** a delivery whose rules produced **no work** (all empty/error) is recorded `status="failed"` (a future "no-op" status is a possible refinement). The HTTP response stays 200.
- **Fallback:** if the webhook is unreachable (localhost not exposed), a **polling fallback** can be enabled per repo (interval check for new/updated PRs) — the toggle exists but is currently **inert** (deferred).
- **Notifications:** triggered tasks appear in the queue in real time; the UI surfaces which event started each task (delivery id, event, timestamp).

### F15. Commit statuses & merge gating *(Phase 2 — owner-authorized edit, 2026-08-09)*

Mirrors GitHub Actions' ability to gate merges on agent results. (Schema placeholders `repos.check_runs_enabled` and `tasks.check_run_id` already exist; the `check_runs` table is Jalebi's registry of the statuses it set.)

- **Mechanism: commit statuses, not check runs.** GitHub's *check-runs* API is **GitHub-App only** — PATs (classic and fine-grained) cannot write it, and Jalebi is PAT-driven (§F1). Merge gating therefore uses **commit statuses** (`POST /repos/{owner}/{repo}/statuses/{sha}`), which the PAT **can** write ("Commit statuses read/write" is already a required scope) and which **branch protection can require** — the same merge-gating outcome. *(This edits the earlier draft, which specified the check-runs endpoint; the earlier wording is superseded.)*
- A commit status is keyed by `(sha, context)` on GitHub — posting the same context again **replaces** the previous status for that SHA, which is the "update, don't duplicate" contract. Contexts: `Jalebi / fix` (issue_fix) and `Jalebi / review` (pr_review).
- **Lifecycle:** run start posts `pending`; run terminal posts the final state from the task status — `done → success`, `failed`/`timed_out → failure`, `cancelled`/`interrupted → error`, `needs_approval`/other → `pending` (GitHub treats pending as "not yet green", so it still blocks a merge).
- **Head SHA:** `pr_review` → the PR head SHA (available at run start). `issue_fix` → the pushed `jalebi/<taskId>` head, which exists only after the first push — so the status is set at **publish time** (auto or manual) once the branch exists.
- Because these are real commit statuses, **branch protection** can require them — merging is blocked until the agent's review/fix status is green. Opt-in per repo (user enables "report commit statuses" for the repo via the Repos page and adds the status context to branch protection).
- Failure/success of the underlying task drives the state; a follow-up updates the existing status for the same head (matched by `(sha, context)`). All status API calls are **best-effort** — a GitHub failure is logged and never fails the task.

### F16. Timeouts, retries & auto-recovery

- **Per-task timeout (enforced):** each task has a timeout (default **60 minutes**, configurable per task and per repo; the DB-level `server_default` stays 30 only because SQLite cannot alter it in place — the ORM Python default of 60 always applies). On expiry the child process is killed (SIGTERM → SIGKILL), the task marked `failed`/`timed_out`, and the commit status (if any) completed with `failure`.
- **Stall detection:** a **watchdog** (per-run `stall_timeout_seconds`, live setting, default 600) declares a run **stalled** when it stops emitting output; the task is marked `failed` and auto-recovery restarts it **fresh** (a wedged session re-hangs).
- **Auto-recovery (default ON, bounded, all task types):** on terminal `failed` (incl. stalled) or `timed_out`, recovery dispatches a new run — **resume the last session** with `continue_prompt` for timeout/other failures, **fresh re-run** for stalls, fresh if no resumable session — up to `retry_policy.max_attempts` (default 3), then the task stays `failed` with a give-up note. Failures matching `retry_policy.non_retryable_patterns` fail immediately. Only the first failure and the final give-up/success notify. See §7.5 for the full loop.
- **Derived timeout escalation:** the per-run timeout is computed from `task.retry_count` — `base × timeout_multiplier^attempts` (default multiplier 2), capped at `retry_policy.max_timeout_minutes` (180). `task.timeout_minutes` is never mutated; a `done` run **resets `retry_count`**, so a manual rerun after success starts from the base timeout again.
- **Manual re-run:** a failed/timed-out task can be **re-run** (UI action) — a fresh `run` reusing the same worktree/session where sensible.
- Interrupted runs remain resumable via follow-up (F11).
- **Settings (`retry_policy`):** `{ auto_retry: true, continue_prompt: "continue", timeout_multiplier: 2, max_timeout_minutes: 180, max_attempts: 3, non_retryable_patterns: [...] }`.

### F17. Secret masking in logs

- The PATs (all vault accounts) and any user-marked secret is **automatically masked** in the live console and stored run logs: any occurrence of the secret string is redacted (e.g. `***`), so even if an agent echoes an env var or token, the console never shows it.
- Also masks **env-var values** (see F20) and applies to **notifications** (title/body) before send.
- Implemented at the ingest layer (before events are broadcast/persisted), not as a display-only filter.
- Optional user-supplied extra secret patterns (regex) to mask beyond the PATs (`secret_patterns` setting).

### F18. Artifacts

- Runs may emit **artifacts**: files the agent produced (logs, test reports, coverage, screenshots) captured from the worktree.
- An **artifact store** keeps them per run (uploaded from the worktree at completion), with retention TTL (default 7 days, `artifact_ttl_days`, matching worktree cleanup; configurable).
- UI: artifact list on the task detail page with download links.

### F19. Notifications

- **Channel:** ntfy push (self-hosted or `ntfy.sh`). The endpoint is a **single merged setting** (`ntfy_topic`) — either a bare topic name (default `https://ntfy.sh` server) or a full URL to a self-hosted server.
- **Rendering (JSON publishing, docs.ntfy.sh/publish/):** notifications POST a JSON body to the **server root** with `topic` inside it — never to `/topic`. Messages are **Markdown**, carry tags, an optional priority, a **click action** and an **"Open task" action button** deep-linking to the Jalebi task page.
- **Events (each a toggle, defaults on):** task **done** (final agent message + summary), task **failed/timed out/cancelled**, task **needs approval** (publish failed / pending manual publish), and **progress** — a periodic "still running" ping every `notify_progress_interval_minutes` (default 30) with elapsed time and the agent's latest message. Plus a **failed-login** alert (§F13).
- **Test:** a "Send test notification" button validates the endpoint (`POST /api/notify/test`).
- **Masking:** notification title/body are run through the secret masker before send. Sending is **best-effort** — a dead ntfy server never fails a task.

### F20. Environment variables for agents

- Owners store named **environment variables** that task agents need to build/test/develop (DB URLs, API keys, tokens), scoped **globally or per-repo**.
- Values are **secrets**: never returned in full by the API (masked previews), added to the secret masker so an agent echoing them is redacted, and injected into the agent subprocess env **on top of** Jalebi's pinned env (they cannot override the token/identity/git hygiene).
- Tasks **select** which variables to inject (checkbox chips on the new-task form); the selection is stored on the task (`tasks.env_vars_json`) and applied to runs and follow-ups.
- `.env` files can be **imported** (paste → parse `KEY=VALUE` → upsert) in Settings.

### F21. Reliability

- **Restart recovery:** runs + sessions are persisted; interrupted runs are marked `interrupted` and remain resumable via follow-up. Migrations run at startup. On restart, stale worktree registrations are pruned so a task id whose worktree was deleted never fails `worktree add`.
- **Live settings:** concurrency, stall timeout, and retry policy are read per run from the persisted `settings` table (not process-time constants) — no restart needed for settings changes.
- **Bounded children:** the number of concurrent child processes is capped at the configured concurrency; abort kills with a timeout then SIGKILL.
- **Watchdog:** the stall watchdog (§F16) bounds runs that stop reporting.
- **Webhook idempotency:** deliveries are idempotent (§F14); replay never double-runs.

---

## 9. Architecture

```
┌──────────────────────────── Your machine (127.0.0.1) ───────────────────────────┐
│                    ▲ GitHub webhook events (via tunnel/proxy)                    │
│                    │                                                             │
│  ┌─────────────────┼──────┐  HTTP (REST + SSE)   ┌────────────────────────────┐  │
│  │  React + Vite UI │      │◀───────────────────▶│  Orchestrator (Flask/Py)    │  │
│  │  queue | task    │      │                     │  ┌──────────────────────┐  │  │
│  │  | diff | agents │      │                     │  │ Webhook listener     │  │  │
│  │  | triggers      │      │                     │  │  (validate + dedup   │  │  │
│  │  | settings      │      │                     │  │   + match rules)     │  │  │
│  └──────────────────┼──────┘                     │  └──────────┬───────────┘  │  │
│                     │                            │             │              │  │
│                     │                            │  ┌──────────▼───────────┐  │  │
│                     │                            │  │ Task queue + workers │  │  │
│                     │                            │  │ (default 4 parallel) │  │  │
│                     │                            │  └──────────┬───────────┘  │  │
│                     │                            │             │              │  │
│                     │                            │  ┌──────────▼───────────┐  │  │
│                     │                            │  │ Agent adapters        │  │  │
│                     │                            │  │  opencode (v1)        │  │  │
│                     │                            │  │  codex (Phase 3)      │  │  │
│                     │                            │  │  claude (Phase 3)     │  │  │
│                     │                            │  └──────────┬───────────┘  │  │
│                     │                            │             │ spawn        │  │
│                     │                            │  ┌──────────▼───────────┐  │  │
│                     │                            │  │ Git workspace mgr     │  │  │
│                     │                            │  │  bare mirror +        │  │  │
│                     │                            │  │  per-task worktrees   │  │  │
│                     │                            │  └──────────┬───────────┘  │  │
│                     │                            │  ┌──────────▼───────────┐  │  │
│                     │                            │  │ Scheduler (Phase 2)   │  │  │
│                     │                            │  │  → screening runs     │  │  │
│                     │                            │  │  → ntfy notifications │  │  │
│                     │                            │  └──────────────────────┘  │  │
│                     │                            │  SQLite (data.db)          │  │
│                     │                            │  + artifacts + secrets     │  │
│                     │                            └────────────┬───────────────┘  │
│                     │                                         │ httpx (PAT vault)│
└─────────────────────┼─────────────────────────────────────────┼──────────────────┘
                      │                                         ▼
                      └─ webhook registration / commit statuses ─▶ github.com
                                          (fetch refs, push branch, open PR,
                                           PR review comments, issues)
```

**Component responsibilities:**

| Component                         | Responsibility                                                                                                                                         |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **UI (React)**              | Queue, task detail (timeline/logs/diff/follow-ups/PR card), agents catalog editor, triggers, settings. Consumes REST; subscribes to SSE event stream. |
| **Orchestrator (Flask)**    | REST + SSE; task queue + worker pool; run lifecycle; auto-recovery; publish; follow-up dispatch; reviewer orchestration; **webhook handling (validate + dedup + match rules)**; **commit-status lifecycle** *(Phase 2)*; timeouts/retries; secret masking; artifact capture. |
| **Adapters**                | Translate a CLI into the `AgentAdapter` protocol (start/resume/list_models/parse). Only component that knows the CLI binary. |
| **Git workspace mgr**       | Bare mirrors, worktree/review-worktree create/discard, branch naming (`jalebi/<taskId>`), push with token credential helper, ref-prefetch for screenings *(Phase 2)*. |
| **GitHub client (httpx)**   | Issues, PRs, reviews, comments, refs, **webhook registration**, **commit statuses** *(Phase 2)*; all PAT-vault-authenticated. |
| **Webhook listener**        | Local Flask endpoint receiving GitHub events (optionally tunneled/reverse-proxied); forwards validated deliveries to the orchestrator. |
| **Scheduler**               | *(Phase 2)* Cron screening runs + dedup + notifications. (Triggering is webhook-driven, NOT scheduler-driven.) |
| **Storage**                 | SQLite schema above (SQLAlchemy 2 + Alembic); secrets file; artifact store. |

**Streaming:** backend pushes normalized `AgentEvent`s over a per-task SSE channel (`GET /api/tasks/:id/events`); the UI renders timeline/logs/diffs live.

**Shipped stack:** Python 3.13 + Flask + SQLAlchemy 2 (SQLite) + Alembic + httpx + APScheduler *(Phase 2)*, managed with `uv`; React + Vite + Tailwind. SSE for events. Git CLI (not libgit2) for repo ops. The built UI is served by Flask on the **same port as the API** (default `2052`; `JALEBI_PORT` overrides) — a single origin.

---

## 10. Data model (summary)

> Shipped schema (authoritative source: `apps/server/src/jalebi/db.py`). Phase-2 tables marked.

```
repos(id, full_name, default_branch, clone_url, pat_scope, pat_name, connected,
      webhook_registered, poll_fallback, check_runs_enabled, last_checked_at)
      -- check_runs_enabled: report commit statuses (PRD F15); pat_name = the named vault account for this repo

tasks(id, type[issue_fix|pr_review|freeform|screen_finding|triggered], repo_id,
      source_branch, target_branch, agent_id, model, cli, prompt,
      pat_name, issues_json, prs_json, context_json, env_vars_json,
      status[queued|running|waiting_review|needs_approval|done|failed|timed_out|interrupted|cancelled],
      timeout_minutes, retry_count, pr_number, check_run_id, publish_mode[auto|manual|NULL],
      created_at, updated_at)
      -- agent_id: catalog slug, FK-less by design (SQLite batch-rebuild hazard); validated in service layer
      -- check_run_id: latest commit-status row id (FK-less by design; see check_runs below)
      -- timeout_minutes: ORM default 60; DB server_default 30 (SQLite can't alter in place — deliberate divergence)
      -- publish_mode: NULL → fall back to global auto_publish; issue_fix defaults auto, others manual

runs(id, task_id, seq, session_id, cli, model, pat_name, pid,
     started_at, finished_at, status, steps_json, artifacts_json, diff_text)

followups(id, task_id, run_id, body, pat_name, model, created_at)

artifacts(id, run_id, path, size, created_at)

catalog_agents(id [slug PK], name, kind[general|reviewer], cli, model,
               personality_md, skills_json, custom_instructions, enabled, created_at)

review_assignments(id, task_id, agent_id, run_id, pr_number, repo_id,
                   status[queued|running|posted|failed], created_at)
      -- UNIQUE(repo_id, pr_number, agent_id): last line of defense vs concurrent duplicate assignments

trigger_rules(id, repo_id [FK CASCADE], event, action, branch_filter, label_filter,
              author_filter, agent_ids_json, custom_instructions, enabled, created_at)

event_deliveries(id, github_delivery_id UNIQUE, event, action, repo_id, repo_full_name,
                 payload_json, received_at, status, result)   -- idempotency + replay (no matched_rule_id)

env_vars(id, name, value, repo_id NULL=global, created_at, updated_at)
      -- UNIQUE(name, repo_id); values masked at the API

settings(key PK, value)   -- see §10.1 for the key list

-- Phase 2 (shipped):
check_runs(id, task_id, run_id, repo_id, head_sha, name, status, conclusion, github_check_id)
      -- Jalebi's registry of the commit statuses it set (one row per (task_id, head_sha, context));
      -- tasks.check_run_id points at the latest row, FK-less by design (SQLite batch-rebuild hazard)
screenings(id, repo_id, name, system_prompt, cadence_cron, scope_branch, enabled, notify_ntfy)
screening_runs(id, screening_id, head_sha, status, started_at, finished_at, findings_json)
      -- findings are stored as JSON on the run (no separate findings table)
```

### 10.1 Settings keys (source: `apps/server/src/jalebi/settings.py`)

| Key | Default | Meaning |
|-----|---------|---------|
| `concurrency` | `4` | Max parallel tasks (0 = paused queue) |
| `auto_publish` | `true` | Global publish policy (per-task `publish_mode` overrides) |
| `ntfy_topic` | `""` | Merged ntfy endpoint (bare topic or full URL) |
| `default_timeout_minutes` | `60` | Base per-task timeout |
| `retry_policy` | `{"auto_retry": true, "continue_prompt": "continue", "timeout_multiplier": 2, "max_timeout_minutes": 180}` | Auto-recovery config (see §F16) |
| `stall_timeout_seconds` | `600` | No-output threshold before a run is declared stalled |
| `secret_patterns` | `[]` | Extra regexes to mask beyond PATs/env vars |
| `artifact_ttl_days` | `7` | Artifact + worktree retention |
| `agent_cli` | `"opencode"` | Backend selection (one-line) |
| `notify_on_done` / `notify_on_failed` / `notify_on_progress` / `notify_on_needs_approval` | `true` | Notification event toggles |
| `notify_progress_interval_minutes` | `30` | Progress-ping interval |
| `webhook_url` | `""` | Public base URL GitHub can reach for webhooks (tunnel) |
| `webhook_secret` | `""` | HMAC secret for `X-Hub-Signature-256` (write-only in the API) |

---

## 11. Screens & flows (UI summary)

1. **Settings:** named PAT vault (validate + show granted scopes), concurrency (default 4), publish policy (default auto), **notifications (ntfy endpoint + per-event toggles + progress interval + test button + failed-login alerts)**, **environment variables (global + per-repo, `.env` import, masked)**, data-dir path, **default timeout (60 min) + retry policy + stall timeout**, **secret patterns**, **tunnel/webhook setup status**.
2. **Agents:** catalog list; create/edit agent (name, kind, cli, model, personality, skills, custom instructions). Skills come from a library of markdown files the user uploads or references by path.
3. **New Task modal:** repo → type → branches (per §F8) → agent (default build agent or catalog agent) → model → instructions (issue #, PR #, or free text). Reviewer multi-select for `pr_review`. *(Screenings get an explicit "run screen now" action in Phase 2.)*
4. **Task detail:** timeline, console (with **masked secrets**), diff, **artifacts**, PR card (publish status + modes, reviewers + assign + **address reviewers** + **commit-status** *(Phase 2)*), follow-up composer, **Re-run** action.
5. **Triggers tab:** per-repo webhook status, trigger rules editor (event → action → agents → filters), delivery log with replay, polling-fallback toggle (inert).
6. **Screenings tab** *(Phase 2)*: screen cards with enable/toggle, cadence editor, last run + findings, "new task from finding".
7. **Notifications:** in-app unread badge + ntfy push for screening findings, reviewer completion, triggered-task failures, and failed-login attempts.

---

## 12. Roadmap

**Phase 0 — Foundation (v1, opencode only)** — **complete (hardened).**

- Repo scaffolding, config, SQLite schema, named PAT vault + validation.
- Git workspace manager (mirror + worktrees + push with token).
- AgentAdapter interface + **opencode adapter** (`--format json`, `--dir`, `--model`, `--session`).
- Task queue with default concurrency 4 (configurable); run lifecycle; cancellation; restart recovery.
- GitHub publish (auto by default; manual override) + `Closes #N`.
- **Timeouts (default 60 min) + re-run/retry; secret masking in logs; artifact capture + retention; ntfy notifications; env vars for agents.**
- Minimal-but-Jules-like UI: queue, task detail (timeline + console + diff + follow-up composer).
- Follow-up via `opencode run --session <id>` (openCode resume).

**Phase 1 — Agent catalog & reviewers + event-driven triggers** — **complete.**

- Catalog agents (personality → AGENTS.md injection + skills references + custom instructions).
- Reviewer workflow: per-reviewer worktrees, PR review comment posting, status tracking, dedup.
- "Address reviewers' comments" follow-up flow.
- **Event-driven triggers:** webhook listener + delivery dedup, trigger-rules editor, webhook registration, delivery log + idempotent replay, and the core flow — `pull_request.opened` ⇒ assigned reviewers auto-start reviewing in real time. (Polling fallback deferred/inert.)
- Hardening: auto-recovery on failures/stalls + review-worktree re-sync (Steps 49/49b).

**Phase 2 — Screening + merge gating** — **complete.**

- **Screening engine:** starter catalog, cron scheduler (built-in, no dependency), HEAD-baseline dedup, structured findings, in-app + ntfy notify, "new task from finding".
- **Commit statuses on head SHAs** (PAT-writable; the check-runs API is GitHub-App-only) so branch protection can gate merges on agent reviews/fixes.
- Diff-view polish + task history.

**Phase 3 — Backend parity**

- **Codex adapter** (`codex exec`, `codex exec resume`, `--json`, `-m/--model`; document the cannot-change-model-on-resume quirk in the UI).
- **Claude Code adapter** (`-p`, `stream-json`, `--resume`).
- Model dropdown per task sourced from `listModels()` for all adapters.
- Optional: shared `opencode serve` backend (attach) to avoid per-run MCP cold boots.

---

## 13. Non-functional requirements

- **Latency:** SSE events render in the UI in near-real-time; console streams without buffering delays. Webhook → task-start latency should be sub-second (validation + dedup only; no slow processing on the webhook path).
- **Reliability:** runs + sessions persisted; interrupted runs resumable; follow-ups work after restart; webhook deliveries **idempotent** (re-delivery never double-runs); commit-status states converge to the final task state *(Phase 2)*; runs auto-recover on failure/timeout/stall (see §F16).
- **Resource safety:** worktree cleanup TTL; cap on concurrent children (== configured concurrency); process kill on abort with timeout then SIGKILL; per-task timeout (default 60 min) prevents runaway agents; stall watchdog bounds runs that stop reporting.
- **Security:** localhost bind; 0600 secrets; PATs never logged/leaked; no secrets interpolated into prompts; optional UI password + failed-login alerts; **automatic secret masking in logs/console**; webhook signature verification when a secret is configured; agent `gh`-guard + external-directory denial.
- **Testability:** adapters unit-tested with mocked CLI output; queue tested with fake agents; GitHub interactions mocked via the httpx `_request` seam; screening scheduler tested with fake clocks *(Phase 2)*; webhook handler tested with fixture payloads + delivery-id dedup.
- **Portability:** must run on Linux and macOS (dev may build on any machine); document Python version + CLI install requirements per adapter.

---

## 14. Known risks & open questions

1. **CLI output drift** — adapter parsers depend on CLI formats (`opencode --format json`, codex `--json`, claude `stream-json`) that may change across CLI versions. Mitigate: pin documented CLI versions, defensive parsing, show raw lines on parse failure.
2. **Codex resume model lock** — follow-ups on a codex-backed task cannot change the model. Decide in Phase 3: surface as a warning, or auto-fork a fresh run when the user changes the model on a follow-up.
3. **Auto-publish safety** — default auto-publish means tasks push to GitHub unattended. Keep `auto_publish` toggle prominent; consider a per-repo "require approval" override for sensitive repos.
4. **PAT scope limits** — fine-grained PATs must include both Contents and Pull requests scopes for the full flow; validation step in Settings enumerates exactly which scope is missing.
5. **Concurrency vs. API rate limits** — 4 parallel agents can burn GitHub/LLM rate limits; consider a per-provider throttle later.
6. **Screening false positives** — findings are LLM-generated; severity should be labeled and the "new task from finding" flow should let the user edit the prompt before starting. *(Phase 2.)*
7. **Bounded auto-recovery** — recovery stops after `retry_policy.max_attempts` (default 3) with a give-up note, and deterministic failures (wrong model, bad auth) fail immediately via `non_retryable_patterns`. Residual risk: a flaky task still burns 3 escalated runs. Mitigations: escalating timeout, first-failure + give-up notifications, manual cancel stays cancelled.
8. **Should Jalebi use a GitHub App instead of PAT for higher rate limits & org-install reach?** — deferred, documented as future option.
9. **Webhook reachability** — a localhost app can't receive GitHub webhooks without a tunnel/reverse proxy. Mitigate: detect unreachable webhook, warn in UI, offer per-repo polling fallback (currently inert); the tunnel is the owner's responsibility (documented in Settings).
10. **Missed/reordered webhook events** — dedup handles re-delivery but not "never delivered"; polling fallback and a "replay delivery" log close the gap for critical triggers (e.g. PR opened).

---

## 15. Appendix — Chanakya workflow reference (for the Jalebi developer)

Full reference of the existing automation that Jalebi replaces the *interaction model* of (not the codebase). All facts below are from `.github/workflows/chanakya.yml` in `samosa-ai-com/Gotcha`.

- **Events:** `issue_comment` (created/edited) and `pull_request` (opened/synchronize/reopened), filtered to commands.
- **Runner:** self-hosted, labels `[self-hosted, chanakya]`; a single runner instance ⇒ sequential job execution.
- **Jobs & commands:** see §2.1 table. `fix` runs the full branch→fix→PR pipeline via the opencode handler; other jobs post comments.
- **Env vars used by `opencode github run`:** `TOKEN`, `EVENT` (webhook payload), `SHARE=false`, `USE_GITHUB_TOKEN=true`, `MODEL`, `PROMPT` (mapped command), `SYSTEM_PROMPT` (repo rules), `AGENT` (opencode agent, e.g. `build`), `OPENCODE_CONFIG_DIR`, `OPENCODE_DISABLE_AUTOUPDATE`, `OPENCODE_FORCE_GIT_CONFIG`, `OPENCODE_CLIENT`.
- **Hardening in place:** strict `startsWith('/chanakya …')` command gates; `comment.user.type != 'Bot'`; trigger comment body prepended to `PROMPT`.
- **Known caveat:** `fix` pushes with the token while `USE_GITHUB_TOKEN=true` skips git credential config in the handler — may fail on fresh runners.
- **opencode `github run` flags:** `--event <json>` (mock event), `--token <pat>` — Jalebi can reuse this handler's *logic* (clone → branch → run → commit → push → PR with `Co-authored-by: <actor> <actor>@users.noreply.github.com`) but must **not** reuse the workflow/runner/comment machinery.

**Why this matters to Jalebi:** the branch/commit/push/PR mechanics and the prompt-construction conventions (title+body+comments as context; `Closes #N`; footer links; emoji reaction lifecycle) are proven and worth mirroring in the orchestrator — minus GitHub comments as the trigger and the emoji reaction lifecycle.

---

## 16. Reference projects & inspiration

The projects below are **inspiration, not dependencies.** During development, study them for ideas and copy only the specific snippets that directly serve a feature. Do **not** pull them in as libraries/submodules, do **not** import their architectures wholesale, and keep Jalebi's own implementation logically simple (see Goal #10). Anything borrowed must be small, self-contained, and re-written in Jalebi's own style.

### 16.1 Agent execution & CLI backends

| Project | Repo | Why inspect / what to learn |
|---|---|---|
| **OpenCode CLI** | `opencode-ai/opencode` | Primary v1 CLI backend. See how `opencode run -f json` emits event lines, session resumption via `--session <id>`, `.opencode/commands/` loading, and tool-permission handling. |
| **OpenHands** (formerly OpenDevin) | `All-Hands-AI/OpenHands` | Leading open-source agent workspace. Learn how its runtime layer isolates terminal commands, tracks state, and turns raw tool calls into a unified web-UI event stream. |
| — bonus: **openhands-resolver** | `All-Hands-AI/openhands-resolver` | Automated batch resolver: GitHub issues → resolved PRs. Useful for Jalebi's issue-fix flow. |
| **Aider** | `Aider-AI/aider` | Standard for programmatic local-git + multi-file LLM editing. Learn diff tracking, automatic commit construction, repo-map graph for token efficiency, and non-interactive flags (`--no-auto-commits`, `--yes`). |
| **SWE-agent** | `SWE-bench/SWE-agent` | Princeton's agent framework for resolving real GitHub issues. Learn file parser, context-window management, and isolated bash/git tool execution. |

### 16.2 Webhook listener, review automation & commit statuses

| Project | Repo | Why inspect / what to learn |
|---|---|---|
| **Qodo PR-Agent** (CodiumAI) | `qodo-ai/pr-agent` | Standard for automated PR reviews / issue triage / auto-commenting. Learn webhook event parsing (`pull_request`, `synchronize`, `issue_comment`), structuring multi-file git diffs for LLMs, and inline PR comments via the GitHub API. |
| **Sweep AI** | `sweepai/sweep` | Early "junior developer" bot for issue→PR workflows. Learn webhook handling, issue parsing, and branch naming (`sweep/...`). |

### 16.3 Git worktree & workspace management

| Project | Repo | Why inspect / what to learn |
|---|---|---|
| *(in-repo)* | `apps/server/src/jalebi/git_workspace.py` | Shipped implementation: bare mirror + worktrees + `--force-with-lease` push with a token credential helper. No external git wrapper is used. |
| *Search* | git worktree wrappers / lifecycle | Look for `git worktree prune` recovery of orphaned worktrees on app restart. |

### 16.4 UI dashboard & real-time SSE streaming

| Project | Repo | Why inspect / what to learn |
|---|---|---|
| **Open-Canvas** | `langchain-ai/open-canvas` | Open-source document/code editing UI (Next.js/React). Learn side-by-side diff viewers, streaming intermediate agent states, message artifact cards. |
| **react-diff-viewer-continued** | — | Clean side-by-side + unified git diff React component for the diff view. |
| **xterm.js** | — | Standard terminal emulator component for streaming raw stdout/stderr of child-process agent runs. |

---

## 17. Development guidelines & secrets

### 17.1 GitHub token(s) — named PAT vault (placeholder)

- Jalebi authenticates to GitHub with **named personal access tokens** supplied by the owner at runtime, stored in the `0600` `<data-dir>/secrets.json` vault (see §F1). There is **no single primary token** — the account selected for a task/repo is the one used.
- **Placeholder while developing/testing:** configure a token via the git-ignored `.env` file — `JALEBI_GITHUB_TOKEN=<PAT>` (see `.env.example` for the key name and required scopes). `JALEBI_GITHUB_TOKEN` is **masking-only** in the app and is never used to resolve which account runs anything.
- All GitHub calls go through the thin httpx client using the selected account's PAT. Validation at startup/on save confirms the token and lists its granted scopes.

### 17.2 Do not use the `gh` CLI

- **During development of this repository, the `gh` command must NOT be used** for any testing, verification, or other tasks (no `gh auth`, `gh pr`, `gh api`, etc.).
- All GitHub interactions during development/testing must go through the **provided fine-grained token** (via the httpx client, `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"`, or git with a credential helper pointing at the token).
- **Owner-authorized exception (commits only):** the `gh` CLI may be used **only** for local git operations on the Jalebi repo itself (staging, committing, pushing). It never extends to testing/verification against the testing account/repo.
- The same ban is enforced at runtime for agents: the per-worktree `opencode.json` denies `gh`, and the agent env has no `gh` auth (see §F13).

### 17.3 Testing repo & account

- The token is for a **testing repository** that is published under a **different account** associated with the current GitHub login (i.e. not the `samosa-ai-com/Gotcha` account/repo).
- Development and testing happen against that dedicated test repo/account only. Do not point any code, tests, or manual checks at production/user repos.
