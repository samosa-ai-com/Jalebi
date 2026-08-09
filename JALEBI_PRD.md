# Jalebi — Product Requirements Document

|                          |                                                                                                                                                 |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Product name**   | Jalebi                                                                                                                                          |
| **Status**         | Draft v1.0                                                                                                                                      |
| **Date**           | 2026-08-06                                                                                                                                      |
| **Owner**          | samosa-ai-com                                                                                                                                   |
| **Repo (planned)** | Separate project —**not** in the Gotcha repo. This PRD is authored in the Gotcha working dir only for convenience and will be relocated. |
| **Versioning**     | Follow this file; feature set is additive                                                                                                       |

---

## 1. Executive summary

Jalebi is a **private, self-hosted, localhost-only web application** that behaves like Google's "Jules (https://jules.google/docs/)": a coding-agent dashboard where the repo owner connects their own GitHub account, creates tasks ("fix this issue", "review this PR", "implement this feature", "audit the security posture of the development branch"), watches the agent work through a live step-by-step timeline, comments to give follow-ups, and gets proactively notified of improvement opportunities. It is also **event-driven**: repo webhooks can auto-start work in real time — the moment a PR is opened, its assigned reviewers from the catalog begin reviewing automatically.

Unlike the existing **Chanakya** automation in the Gotcha repo (GitHub Actions + self-hosted runner + comment-triggered `opencode github run`), Jalebi is **completely independent**:

- No `.github/workflows`, no self-hosted runner, no comment-triggered slash commands. Event automation uses **repo webhooks** delivered to a local listener.
- Uses the owner's **GitHub personal access token (PAT)** directly.
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
| `fix`          | `/chanakya fix`                | Clones the repo, creates a branch, fixes the issue, opens a PR with`Closes #N` |
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
Jalebi:    local web UI / GitHub webhook event ──▶ Jalebi orchestrator ──▶ (spawn CLI agent as child process) ──▶ push/PR/check-run via PAT ──▶ UI updates
```

Everything is local, private to the owner, and driven from the dashboard.

---

## 3. Goals

1. **Jules-like experience, self-hosted.** A dedicated web UI with a task queue, a per-task step-by-step timeline, live streaming logs, an incremental diff view, and a follow-up/comments panel. Owner-only; no comment triggers.
2. **Pluggable agent backends.** v1 ships the **opencode** CLI. Architecture must support adding **Codex** and **Claude Code** (and others) with a **one-line config change** (`agent.cli`) and no changes elsewhere.
3. **Model freedom.** The user can pick any model available to the selected CLI, per task and per agent (no hard-coded model).
4. **Agent catalog with personalities & skills.** Users configure named agents (e.g. "Security Auditor", "Backend Reviewer", "Docs Guru", "Conflict Resolver"). Each is just: a **personality** (markdown injected into the task worktree's `AGENTS.md`) + a set of **skill files** (markdown, referenced by path) + an optional **model** + an optional **CLI**. The underlying default agent (opencode build agent) picks these up automatically during execution.
5. **Reviewer workflow.** Users assign catalog agents as reviewers on a PR. Each reviewer works in its **own local clone**, validates, then posts its comments on the GitHub PR. The user then manually approves/merges, and can send a follow-up to the original fix agent referencing the reviewers' comments.
6. **Branch control.** Every issue-fix / PR task lets the user choose **source branch** and **target branch** (e.g. fix from `main` but commit to `development`).
7. **Proactive screening.** Scheduled, multi-profile audits ("suggestions for improvements") with **per-profile cadence and system prompt**. Screening is **notify-only — it never auto-acts** on issues; the user explicitly chooses to start work.
8. **Privacy & ownership.** Localhost-bound, PAT-authenticated, single-owner.
9. **Event-driven automation (essential).** Repo webhook events can auto-start tasks in real time — e.g. the moment a PR is opened, its assigned reviewers from the catalog begin reviewing automatically. Triggers are **webhook-pushed**, not polled (scheduling is not the mechanism; triggering is).
10. **Simplicity above all.** The implementation must stay **logically simple**. Do not import whole subsystems or frameworks from reference projects just because they exist — borrow only the specific ideas/snippets that directly serve a feature, and prefer the simplest code that satisfies the PRD. Avoid over-engineering (no event-bus frameworks, no complex state machines, no distributed abstractions) unless a requirement literally demands it. If a feature starts feeling complex to implement, stop and revisit the design.

## 4. Non-goals (v1)

- **No** slash-command triggers from GitHub comments (no `/chanakya`-style `issue_comment` listener). Event automation is **webhook-driven** (repo events), not comment-driven.
- **No** multi-user accounts, roles, or team collaboration.
- **No** hosting in the cloud; localhost only.
- **No** GitHub App / OAuth app — PAT only in v1 (a GitHub App can be a future option).
- **No** auto-acting screening (screenings never open issues/PRs or start fixes on their own).
- **No** shipping the Gemini CLI adapter (explicitly replaced by **Codex**).
- **No** replacing Chanakya itself — Chanakya remains in Gotcha; Jalebi is a separate, independent project.

---

## 5. Personas

- **The owner (primary):** owns the repos, connects a PAT, creates tasks, approves PRs, configures agents and screenings. Only persona.
- **The downstream developer:** reads the PRD and builds Jalebi on any machine. This document is their single source of truth for behavior + context.

---

## 6. Glossary

| Term                       | Meaning                                                                                |
| -------------------------- | -------------------------------------------------------------------------------------- |
| **Task**             | A unit of work in Jalebi (fix issue, review PR, free-text instruction, screen).        |
| **Run**              | One agent execution (one CLI child process) within a task.                             |
| **Follow-up**        | A user comment on a task that**resumes** the same agent session.                 |
| **Adapter**          | A backend integration that maps a CLI (opencode/Codex/Claude) to one common interface. |
| **Catalog agent**    | A user-configured named agent = personality + skills + optional model + optional CLI.  |
| **Reviewer**         | A catalog agent of kind`reviewer` assigned to a PR.                                  |
| **Screen/Screening** | A scheduled proactive audit with its own system prompt + cadence.                      |
| **Webhook trigger**  | A repo webhook event (e.g. `pull_request.opened`) that auto-starts task(s) per user rules. |
| **Check run**        | A GitHub commit status reporting a task's queued/in-progress/completed state — can gate merges via branch protection. |
| **Artifact**         | A file produced by a run (log, report, coverage) captured and retained per run.        |
| **Worktree**         | A git worktree — an isolated checkout of a repo for a single task/agent.              |
| **Publish**          | Push branch + open (or update) a PR.                                                   |
| **Resume**           | Continue an existing agent session (CLI-native continuation).                          |

---

## 7. High-level product behavior

### 7.1 Core loop (single task)

1. User connects a PAT (Settings).
2. User creates a task: pick a repo, choose a task type, select **source/target branches**, pick a **catalog agent** (or the default build agent), optionally choose a **model**, and enter instructions (issue number / PR number / free text).
3. Orchestrator creates a **worktree**, writes the personality/skills files, and runs the agent via the selected CLI adapter (`start`).
4. UI streams the agent's **step timeline** (scanning → planning → implementing → testing → creating PR), the **live console**, and the **incremental diff**.
5. On completion, Jalebi **publishes** per the publish policy (default: auto-open PR) and shows the PR link.
6. User can post a **follow-up** → the orchestrator **resumes** the same agent session in the same worktree → agent amends its work → branch/PR updated.
7. User approves/merges the PR manually on GitHub.

### 7.2 Reviewer loop

1. A PR exists (created by a fix task, or an existing external PR).
2. User assigns 1..N reviewers from the catalog.
3. Each reviewer runs in **its own worktree**, checks out the PR branch, reviews with its own personality/skills/model/CLI, validates (builds/tests), then **posts a PR review comment** via the GitHub API.
4. When all reviewers have commented, the user decides (approve, request changes, merge) — **manually**.
5. Optionally, the user sends a **follow-up** to the original fix agent: "Look at the reviewers' comments and fix accordingly." The fix agent resumes, sees the PR comments (fetched from GitHub), and updates the PR.

### 7.3 Screening loop

1. User enables/creates screenings per repo (each with its own system prompt + cron cadence).
2. Scheduler checks cadence; skips if the repo HEAD is unchanged since the last run (baseline dedup).
3. Runs a read-only audit session; parses findings; stores them; **notifies** the user (in-app + optional ntfy push).
4. Findings are browseable; the user can convert a finding into a task — **never auto-started**.

### 7.4 Event-triggered loop (webhook-driven)

1. GitHub delivers a **repo webhook event** to Jalebi's local listener (e.g. `pull_request` opened, `issues` opened, PR updated).
2. The listener validates and **idempotently** dedups the delivery (`X-GitHub-Delivery` / `X-GitHub-Event` headers), then matches it against the user's **trigger rules**.
3. A matching rule creates and enqueues task(s) immediately — e.g. a PR just opened ⇒ each **assigned reviewer** gets its own review task and starts in real time.
4. Runs proceed exactly like a manual task (timeline, logs, diffs) and report back via **check runs** on the PR's head commit when configured.
5. No polling involved; the trigger is the event itself. (A manual "replay last event" button and an optional polling fallback exist for when webhooks can't be configured.)

---

## 8. Detailed features

### F1. GitHub integration (PAT)

- The user supplies a **fine-grained or classic PAT** in Settings.
- Required scopes (document in UI): classic `repo` (or fine-grained: Contents read/write, Pull requests read/write, Issues read/write, Metadata read, **Commit statuses read/write** for check runs).
- Token is stored in a `0600` secrets file on disk (never in the browser; the backend proxies all GitHub calls).
- Capabilities used: repo list & default branch, issues, PRs (create/update/comment/review), refs, clone/push via authenticated git, **repo webhook registration/management**, **commit statuses (check runs)**.
- **Webhook setup:** Jalebi registers repo webhooks via the API (`POST /repos/{owner}/{repo}/hooks`) targeting its local listener (URL + optional secret for signature verification). For a localhost-only install, GitHub cannot reach the machine — the listener must be exposed via a **tunnel (e.g. `cloudflared`/`ngrok`)** or the webhook URL points at a small reverse proxy; the app detects an unreachable webhook and warns, offering the polling fallback.
- **Safety:** the token is never exposed to the frontend or to agent prompts.

### F2. Local web app / UI (Jules-like)

Layout (three areas):

1. **Left rail — Task queue:** list of tasks with status (`queued / running / waiting-review / needs-approval / done / failed`), repo, agent, model, PR link. Filterable (all, running, done, failed, screenings).
2. **Main panel — Task detail:**
   - **Step timeline:** ordered phases of the current run (scanning → planning → implementing → testing → creating PR → reviewing) with timestamps; the agent adapter emits `step` events.
   - **Live console:** streaming stdout of the CLI child process (syntax-highlighted).
   - **Diff viewer:** incremental file diffs as they appear; per-file expand.
   - **Follow-up composer:** free-text comment → resumes the session.
   - **PR card:** status of publish, link to the GitHub PR, reviewers assigned, reviewer comment status.
3. **Top bar — Global nav:** Repos · Tasks · Screenings · Agents · Triggers · Settings.

Non-goal: build nothing on `opencode web`'s chat UI — Jalebi's UI is bespoke and orchestrator-centric (queue + timeline + diffs + reviewers), not a direct agent chat.

### F3. Task queue & concurrency

- **Default concurrency: 4** parallel tasks, **configurable from the UI** (0 = paused queue).
- Worker pool executes the configured max number of tasks concurrently; the rest wait in queue.
- Cancel/abort a running task (kill child process; for opencode use `POST /session/:id/abort` if attached to a server, else terminate the process).
- Tasks survive restarts (persisted in SQLite; interrupted runs are marked `interrupted` and resumable).

### F4. Agent adapters (backend CLI independence)

**Principle (P0):** the entire system depends on one interface; the only place that knows the CLI name is the adapter and a one-line config.

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
| **opencode** (v1)  | `opencode run --dir <ws> --format json [--model <m>] <prompt>`             | `opencode run --session <sessionId> --format json <prompt>`              | `--format json` (event stream; contains `session.id`, `session.updated`, `message.updated`, `session.idle`) | `-m/--model provider/model`; env `MODEL` |
| **codex** (later)  | `codex exec --json [--model <m>] "<prompt>"`                               | `codex exec resume <session_id> "<prompt>"`                              | `--json`; `--output-schema` for structured findings                                                               | `-m/--model` (or `config.toml`)          |
| **claude** (later) | `claude -p "<prompt>" --output-format stream-json --verbose [--model <m>]` | `claude -p "<prompt>" --resume <session_id> --output-format stream-json` | `--output-format stream-json` (`init.session_id` + typed events)                                                  | `--model`                                  |

**Known adapter quirks (must be documented in code + README):**

- **opencode:** resuming keeps the session's original model unless `--model` is passed on resume (supported). `--fork` can fork instead of continuing if the user prefers a clean follow-up.
- **codex:** **on resume, the model/reasoning-effort cannot be changed** — the resumed session retains the original run's settings. Model changes on follow-ups must start a fresh run or be surfaced in the UI.
- **claude:** `--resume <id>` requires the session id captured from the first run (`init.session_id`). `--continue` resumes the last session only (do not rely on it).
- Processes must be spawned with a **working directory = the task worktree** so the CLI discovers `AGENTS.md`/skills.
- Parsing must be **defensive**: unknown/non-parseable lines are shown verbatim in the console rather than crashing.

### F5. Model selection

- **No hard-coded model.** Each task exposes a model dropdown populated from the active adapter's `listModels()`.
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
   - copies the skill files into the worktree (e.g. `.jalebi/agents/<agentId>/skills/*.md`) and **references them from `AGENTS.md` via `@path` links**, plus passes the **directory path** on the run command where the CLI supports it (e.g. `--dir`/cwd, or a skills path flag).
3. The **default agent of the CLI** (e.g. opencode's build agent) reads `AGENTS.md` + skills and uses them opportunistically during execution. No custom agent definitions, no special prompts plumbing.
4. For opencode specifically this also works with its `.claude/skills`-compatible loading (`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` must remain unset) and `AGENTS.md` auto-discovery.

This satisfies: *"They can be at certain location, and you may just inject the address of those in your request when you run the opencode run command. And the default agent of opencode, like the build agent, will automatically pick those personality and skill files if it feels like it needs during the execution."*

**Model/skill independence:** because the personality+skills mechanism is *files in the worktree*, it works identically for opencode, Codex (`AGENTS.md` is read by Codex too), and Claude (`CLAUDE.md`) — the adapter only varies the file/dir conventions.

### F7. Reviewer workflow

1. **Assigning:** on a task's PR card (or a standalone "Review PR" task), the user selects reviewers from the catalog (kind `reviewer`).
2. **Execution:** each reviewer gets its **own worktree** (isolated clone), checks out the PR's head branch, and runs a review session with its own personality/skills/model/CLI (via the same adapter interface — `start`, not `resume`).
3. **Validation:** the reviewer is instructed (via a system prompt injected in its task prompt) to build/test what it touched if feasible, and to report blockers.
4. **Posting:** on completion, the orchestrator posts the reviewer's output as a **PR review comment** on GitHub (using its PAT; e.g. `POST /repos/{owner}/{repo}/pulls/{n}/reviews` with `event: "COMMENT"` and body = the review). The UI tracks which reviewers have posted.
5. **Approval is manual:** Jalebi never approves/merges. When all reviewers have posted, the user approves or requests changes on GitHub directly.
6. **Follow-up to the fixer:** the user can send a follow-up to the original fix agent: the orchestrator **resumes** the fixer's session, and (via its prompt) instructs it to fetch the current PR review comments from GitHub and address them. The fixer amends the branch; the PR updates.

### F8. Branch selection (source/target)

Every issue-fix and PR task exposes **two branch selectors** in the UI (prefilled with sensible defaults):

- **Source branch** — the base to branch off / the branch whose state the worktree starts from.
- **Target branch** — the PR base (`base`), where the fix will land.

Use cases this serves:

- Issue filed against `development` → source `development`, target `development`.
- Issue filed against `main` but the team wants changes staged in `development` → source `main`, target `development` (the fixer works from `main` and opens the PR into `development`).
- Feature work always lands in `development` → default target `development` unless the user overrides.

The orchestrator creates the worktree from **source**, opens the PR with **base = target**, `head = <jalebi>/<taskId>`.

### F9. PR publishing policy

- **Default: auto-publish** — on task completion the orchestrator pushes the branch and opens a PR. Title = agent's `.jalebi/pr.md` first `# <title>` line (fallback: `🦦 Jalebi: <first prompt line>` / `🦦 Jalebi task`); body includes the agent-written `.jalebi/pr.md` body (or raw prompt as fallback) + `Closes #N` when an issue was referenced + the Jalebi brand footer (`🦦 Opened by [Jalebi](https://github.com/samosa-ai-com/jalebi) — your self-hosted AI coding agent by [Samosa AI](https://github.com/samosa-ai-com).`) + `Co-authored-by: Jalebi <jalebi@samosa-ai.com>`. External posts must not contain task IDs, `localhost`, internal URLs, or any other owner-only info — see `docs/14-messaging-strategy.md` for the templates and invariants (supersedes the earlier "link to the Jalebi task" mandate).
- **Three publish modes** — manual publish (`POST /api/tasks/<id>/publish` body `{mode, branch?, pr_number?}`) supports three modes; auto-publish always uses `new_pr`:
  - **`new_pr`** *(default)* — push `jalebi/<id>` and open/reuse a PR into `task.target_branch`.
  - **`update_pr`** — fast-forward (or merge) `jalebi/<id>` into an existing PR's head branch and force-push with `--force-with-lease`. Requires `pr_number` (or `task.prs_json[0]`). No new PR opened; no `Closes #N` comment.
  - **`push_branch`** — fast-forward (or merge) `jalebi/<id>` into a named branch and force-push with `--force-with-lease`. Requires `branch`. No PR interaction.
  - On merge conflict (any mode): `PublishConflict` (409) with the conflicting file list. On remote-moved-since-fetch (`--force-with-lease` refusal): `PushLeaseFailed` (412). UI shows both as clear inline errors. `--force-with-lease` is the only force variant used — never `--force`, so concurrent pushes by others are protected.
- **Smart-default Publish button:** for `freeform` / `screen_finding` / `triggered` tasks with `task.prs_json` non-empty, the manual Publish button reads **"Push to PR #N"** and dispatches `update_pr` for the first linked PR (the common case — work done against an existing PR). Otherwise it reads **"Publish"** and dispatches `new_pr`. An **Advanced** disclosure exposes all three modes + a PR picker (when `prs_json` has >1) + a branch text input for `push_branch`. `issue_fix` always defaults to `new_pr` (its canonical purpose is opening a PR with `Closes #N`); the other modes are still available under Advanced.
- **Configurable:** per-task or global setting `auto_publish: true|false`; when `false`, the UI shows a **"Publish"** button (push + open PR) that the user clicks, and a "push-only" option.
- **PR updates on follow-ups:** follow-ups amend the same branch; existing PR is force-updated (new commit pushed) — never a second PR for the same task.

### F10. Proactive screening (suggestions for improvements)

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
- **Scheduler:** `node-cron` per screen. **Baseline dedup:** store last audited `HEAD` per (repo × screen); skip if unchanged. Run the screen at HEAD with a **read-only prompt** (no edits, no git writes) and an optional structured-output schema for findings.
- **Findings model:** `{ severity, title, file, line?, detail, recommendation }`.
- **Notification:** in-app (screenings tab, unread badge) + optional **ntfy** push (a topic the user sets) — the owner's environment already uses ntfy.
- **UI:** Screenings tab to configure cadence, enable/disable, view history, and **"New task from finding"** (opens a prefilled task — still requires the user's explicit action).

### F11. Follow-ups (resume)

- Any task with a completed run shows a **follow-up composer**.
- Posting a follow-up → orchestrator calls `adapter.resume({ sessionId, prompt: followup + context })` in the **same worktree**, same branch.
- For a reviewer follow-up or "address the reviewers" request, the prompt includes the current GitHub PR review comments (fetched via API) so the agent can see them.
- Follow-ups must be **backend-agnostic** (works for opencode, codex, claude).
- Session ids are persisted per run (`runs.session_id`) so follow-ups survive restarts.

### F12. Storage

- **SQLite** via better-sqlite3 + Drizzle ORM.
- Tables: `repos`, `tasks`, `runs`, `followups`, `catalog_agents`, `screenings`, `screening_runs`, `findings`, `settings` (key-value, incl. concurrency, publish policy, PAT pointer, ntfy topic).
- Data dir (default `~/.jalebi/`): `data.db`, `secrets.json` (0600), `repos/` (bare mirrors), `ws/` (worktrees), `agents/` (catalog agent files), `logs/`.
- Prune policy: delete task worktrees for `done` tasks after a configurable TTL (default 7 days) unless a PR is still open.

### F13. Security & privacy

- Bind server to **127.0.0.1** only. Optional UI password (env `JALEBI_PASSWORD` or `OPENCODE_SERVER_PASSWORD` reuse).
- PAT stored with `0600`; never logged, never sent to the browser, never passed to agent prompts.
- Agents execute arbitrary shell code by design — each child process is scoped to its own worktree (cwd), and a settings toggle can add a sandbox wrapper (e.g. `bwrap`/`firejail`) later.
- Publishing is idempotent; follow-ups only ever touch the task's own branch.
- If the app is ever exposed (tunnel), require the UI password and document the risk.

### F14. Event-driven triggers (webhooks)

Triggering is a first-class, **webhook-pushed** mechanism (not polling, not scheduling).

- **Listener:** a local HTTP endpoint (`POST /webhook`) receiving repo webhook events. Validated by optional `X-Hub-Signature-256` secret; **idempotent** — deliveries deduped on `X-GitHub-Delivery`, so re-deliveries never double-run a task.
- **Trigger rules:** per repo, user-configurable rules of the form:
  - event: `pull_request.opened` | `pull_request.synchronize` | `pull_request.reopened` | `issues.opened` | `pull_request_review` | `push` (…)
  - action: `start_review` | `triage_issue` | `create_task` | `rerun_review`
  - scope: branch filter (optional), labels (optional), PR author (optional)
  - target: which catalog agent(s) / reviewer(s) to launch, with custom instructions.
- **Core use case:** `pull_request.opened` ⇒ auto-start the **assigned reviewers** from the catalog; each begins its own review task immediately. A `synchronize` (new push) can optionally re-trigger a reviewer pass.
- **Reviewer auto-assignment source:** trigger rules may reference a default reviewer set, or reviewers can be assigned per-PR via the UI; the webhook path uses whichever applies.
- **Replay & fallback:** UI offers "replay last delivery" for any event; if the webhook is unreachable (localhost not exposed), a **polling fallback** can be enabled per repo (interval check for new/updated PRs), but webhooks are the default and preferred path.
- **Notifications:** triggered tasks appear in the queue in real time; the UI surfaces which event started each task (delivery id, event, timestamp).

### F15. Check runs & merge gating

Mirrors GitHub Actions' ability to gate merges on agent results.

- Jalebi creates **check runs** (commit statuses) on the head SHA of the branch it's working on, via the PAT (`POST /repos/{owner}/{repo}/check-runs`).
- Lifecycle mirrors a run: `queued` → `in_progress` (with a friendly name like `Jalebi / review (security-auditor)`) → `completed` with `conclusion` (`success`/`failure`/`neutral`/`cancelled`).
- Because these are real check runs, **branch protection** can require them — merging is blocked until the agent's review/fix check is green. This is opt-in per repo (user enables "report check runs" for the repo and adds the check to branch protection).
- Failure/success of the underlying task drives the conclusion; a follow-up updates the existing check rather than creating duplicates (matched by name + head SHA).

### F16. Timeouts & retries

- **Per-task timeout (enforced):** each task has a timeout (default **60 minutes**; revised from 30 to suit longer agent runs), configurable per task and per repo. On expiry the child process is killed (SIGTERM → SIGKILL), the task marked `failed`/`timed-out`, and the check run (if any) completed with `failure`.
- **Retries:** a failed/timed-out task can be **re-run** (UI action) — a fresh `run` reusing the same worktree/session where sensible, or a new run when the CLI requires it (e.g. a fresh `opencode run`). Retry count is tracked; auto-retry on transient failures (e.g. network) is configurable (default off for publishing tasks, on for pure-review tasks).
- Interrupted runs remain resumable via follow-up (F11).

### F17. Secret masking in logs

- The PAT (and any user-marked secret) is **automatically masked** in the live console and stored run logs: any occurrence of the secret string is redacted (e.g. `***`), so even if an agent echoes an env var or token, the console never shows it.
- Implemented at the ingest layer (before events are broadcast/persisted), not as a display-only filter.
- Optional user-supplied extra secret patterns (regex) to mask beyond the PAT.

### F18. Artifacts

- Runs may emit **artifacts**: files the agent produced (logs, test reports, coverage, screenshots) captured from the worktree.
- An **artifact store** keeps them per run (uploaded from the worktree at completion), with retention TTL (default 7 days, matching worktree cleanup; configurable).
- UI: artifact list on the task detail page with download links.

### F19. Notifications

- **Channel:** ntfy push (self-hosted or `ntfy.sh`). The endpoint is a **single merged setting** — either a bare topic name (default `https://ntfy.sh` server) or a full URL to a self-hosted server.
- **Rendering (JSON publishing, docs.ntfy.sh/publish/):** notifications POST a JSON body to the **server root** with `topic` inside it — never to `/topic` (that would show raw JSON as the message). Messages are **Markdown**, carry tags, an optional priority, a **click action** and a **"Open task" action button** deep-linking to the Jalebi task page.
- **Events (each a toggle, defaults on):** task **done** (final agent message + summary), task **failed/timed out/cancelled**, task **needs approval** (publish failed / pending manual publish), and **progress** — a periodic "still running" ping every `notify_progress_interval_minutes` (default 30) with elapsed time and the agent's latest message.
- **Test:** a "Send test notification" button validates the endpoint (`POST /api/notify/test`).
- **Masking:** notification title/body are run through the secret masker before send, so a stray PAT/env-var value can never reach the push channel. Sending is **best-effort** — a dead ntfy server never fails a task.

### F20. Environment variables for agents

- Owners store named **environment variables** that task agents need to build/test/develop (DB URLs, API keys, tokens), scoped **globally or per-repo**.
- Values are **secrets**: never returned in full by the API (masked previews), added to the secret masker so an agent echoing them is redacted, and injected into the agent subprocess env **on top of** Jalebi's pinned env (they cannot override the token/identity/git hygiene).
- Tasks **select** which variables to inject (checkbox chips on the new-task form); the selection is stored on the task and applied to runs and follow-ups.
- `.env` files can be **imported** (paste → parse `KEY=VALUE` → upsert) in Settings.

---

## 9. Architecture

```
┌──────────────────────────── Your machine (127.0.0.1) ───────────────────────────┐
│                    ▲ GitHub webhook events (via tunnel/proxy)                    │
│                    │                                                             │
│  ┌─────────────────┼──────┐  HTTP (REST + SSE)   ┌────────────────────────────┐  │
│  │  React + Vite UI │      │◀───────────────────▶│  Orchestrator (Node+Hono)  │  │
│  │  queue | task    │      │                     │  ┌──────────────────────┐  │  │
│  │  | diff | agents │      │                     │  │ Webhook listener     │  │  │
│  │  | screenings    │      │                     │  │  (validate + dedup   │  │  │
│  │  | triggers      │      │                     │  │   + match rules)     │  │  │
│  │  | settings      │      │                     │  └──────────┬───────────┘  │  │
│  └──────────────────┼──────┘                     │             │              │  │
│                     │                            │  ┌──────────▼───────────┐  │  │
│                     │                            │  │ Task queue + workers │  │  │
│                     │                            │  │ (default 4 parallel) │  │  │
│                     │                            │  └──────────┬───────────┘  │  │
│                     │                            │             │              │  │
│                     │                            │  ┌──────────▼───────────┐  │  │
│                     │                            │  │ Agent adapters        │  │  │
│                     │                            │  │  opencode (v1)        │  │  │
│                     │                            │  │  codex (later)        │  │  │
│                     │                            │  │  claude (later)       │  │  │
│                     │                            │  └──────────┬───────────┘  │  │
│                     │                            │             │ spawn        │  │
│                     │                            │  ┌──────────▼───────────┐  │  │
│                     │                            │  │ Git workspace mgr     │  │  │
│                     │                            │  │  bare mirror +        │  │  │
│                     │                            │  │  per-task worktrees   │  │  │
│                     │                            │  └──────────┬───────────┘  │  │
│                     │                            │  ┌──────────▼───────────┐  │  │
│                     │                            │  │ Scheduler (node-cron) │  │  │
│                     │                            │  │  → screening runs     │  │  │
│                     │                            │  │  → ntfy notifications │  │  │
│                     │                            │  └──────────────────────┘  │  │
│                     │                            │  SQLite (data.db)          │  │
│                     │                            │  + artifacts + secrets     │  │
│                     │                            └────────────┬───────────────┘  │
│                     │                                         │ Octokit (PAT)    │
└─────────────────────┼─────────────────────────────────────────┼──────────────────┘
                      │                                         ▼
                      └─ webhook registration / check runs ─▶ github.com
                                          (fetch refs, push branch, open PR,
                                           PR review comments, issues)
```

**Component responsibilities:**

| Component                         | Responsibility                                                                                                                                         |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **UI (React)**              | Queue, task detail (timeline/logs/diff/follow-ups), agents catalog editor, screenings config, **triggers**, settings. Consumes REST; subscribes to SSE event stream. |
| **Orchestrator (Hono)**     | REST + SSE; task queue + worker pool; run lifecycle; publish; follow-up dispatch; reviewer orchestration; baseline bookkeeping; **webhook handling (validate + dedup + match rules)**; **check-run lifecycle**; timeouts/retries; secret masking; artifact capture. |
| **Adapters**                | Translate a CLI into the`AgentAdapter` interface (start/resume/listModels/parse). Only component that knows the CLI binary.                          |
| **Git workspace mgr**       | Bare mirrors, worktree create/discard, branch naming (`jalebi/<taskId>`), push with token credential helper, ref-prefetch for screenings.            |
| **GitHub client (Octokit)** | Issues, PRs, reviews, comments, refs, **webhook registration**, **check runs**; all PAT-authenticated.                                                                                           |
| **Webhook listener**        | Local endpoint receiving GitHub events (optionally tunneled/reverse-proxied); forwards validated deliveries to the orchestrator. |
| **Scheduler**               | Cron screening runs + dedup + notifications. (Triggering is webhook-driven, NOT scheduler-driven.)                                                                                           |
| **Storage**                 | SQLite schema above; secrets file; artifact store.                                                                                                                     |

**Streaming:** backend pushes normalized `AgentEvent`s over a per-task SSE channel (`GET /api/tasks/:id/events`); the UI renders timeline/logs/diffs live.

**Recommended stack (open to implementer choice):** Node ≥22 + TypeScript + Hono + better-sqlite3 + Drizzle ORM + Octokit + node-cron + React + Vite + Tailwind. SSE for events. Git CLI (not libgit2) for repo ops.

---

## 10. Data model (summary)

```
repos(id, full_name, default_branch, clone_url, pat_scope, webhook_registered, poll_fallback, check_runs_enabled, last_checked_at)
tasks(id, type[issue_fix|pr_review|freeform|screen_finding|triggered], repo_id, source_branch,
      target_branch, agent_id, model, cli, prompt, status[queued|running|waiting_review|
      needs_approval|done|failed|timed_out|interrupted], timeout_minutes, retry_count,
      pr_number, check_run_id, created_at, updated_at)
runs(id, task_id, seq, session_id, cli, model, started_at, finished_at, status, steps_json, artifacts_json)
followups(id, task_id, run_id, body, created_at)
catalog_agents(id, name, kind[general|reviewer], cli, model, personality_md, skills_json,
               custom_instructions, enabled, created_at)
review_assignments(id, task_id, agent_id, run_id, pr_number, status[queued|running|posted|failed])
trigger_rules(id, repo_id, event, action, branch_filter, label_filter, author_filter,
              agent_ids_json, custom_instructions, enabled)
event_deliveries(id, github_delivery_id UNIQUE, event, repo_id, payload_json,
                 received_at, matched_rule_id, status, result)   -- idempotency + replay
check_runs(id, task_id, run_id, repo_id, head_sha, name, status, conclusion)
screenings(id, repo_id, name, system_prompt, cadence_cron, scope_branch, enabled, notify_ntfy)
screening_runs(id, screening_id, head_sha, status, started_at, finished_at, findings_json)
artifacts(id, run_id, path, size, created_at)
settings(key, value)   -- concurrency, auto_publish, ntfy_topic (merged endpoint),
                          default_timeout_minutes, retry_policy, secret_patterns_json,
                          artifact_ttl_days, notify_on_* toggles, etc.
env_vars(id, name, value, repo_id NULL=global, created_at, updated_at)  -- agent env vars (masked at API)
tasks.env_vars_json     -- selected env-var names injected into the agent subprocess env
```

---

## 11. Screens & flows (UI summary)

1. **Settings:** PAT (validate + show granted scopes), concurrency (default 4), publish policy (default auto), **notifications (ntfy endpoint + per-event toggles + progress interval + test button)**, **environment variables (global + per-repo, `.env` import, masked)**, data-dir path, **default timeout (60 min) + retry policy**, **secret patterns**, **tunnel/webhook setup status**.
2. **Agents:** catalog list; create/edit agent (name, kind, cli, model, personality, skills, custom instructions). Skills come from a library of markdown files the user uploads or references by path.
3. **New Task modal:** repo → type → source/target branches → agent (default build agent or catalog agent) → model → instructions (issue #, PR #, or free text). For screenings, an explicit "run screen now" action.
4. **Task detail:** timeline, console (with **masked secrets**), diff, **artifacts**, PR card (publish status, reviewers, assign reviewers, **check-run status**), follow-up composer, **Re-run** action.
5. **Triggers tab:** per-repo webhook status, trigger rules editor (event → action → agents → filters), delivery log with replay, polling-fallback toggle.
6. **Screenings tab:** screen cards with enable/toggle, cadence editor, last run + findings, "new task from finding".
7. **Notifications:** in-app unread badge + ntfy push for screening findings, reviewer completion, and triggered-task failures.

---

## 12. Roadmap

**Phase 0 — Foundation (v1, opencode only)**

- Repo scaffolding, config, SQLite schema, PAT settings + validation.
- Git workspace manager (mirror + worktrees + push with token).
- AgentAdapter interface + **opencode adapter** (`--format json`, `--dir`, `--model`, `--session`).
- Task queue with default concurrency 4 (configurable); run lifecycle; cancellation.
- GitHub publish (auto by default; manual override) + `Closes #N`.
- **Timeouts (default 60 min) + re-run/retry; secret masking in logs; artifact capture + retention; ntfy notifications; env vars for agents.**
- Minimal-but-Jules-like UI: queue, task detail (timeline + console + diff), follow-up composer.
- Follow-up via `opencode run --session <id>` (openCode resume).

**Phase 1 — Agent catalog & reviewers + event-driven triggers**

- Catalog agents (personality → AGENTS.md injection + skills references + custom instructions).
- Reviewer workflow: per-reviewer worktrees, PR review comment posting, status tracking.
- "Address reviewers' comments" follow-up flow.
- **Event-driven triggers:** webhook listener + delivery dedup, trigger-rules editor, webhook registration (or polling fallback), and the core flow — `pull_request.opened` ⇒ assigned reviewers auto-start reviewing in real time.

**Phase 2 — Branch control + screening + merge gating**

- Source/target branch selectors wired through worktree creation and PR base/head.
- Screening engine: starter catalog, cron scheduler, HEAD-baseline dedup, structured findings, in-app + ntfy notify, "new task from finding".
- **Check runs / commit statuses on head SHAs so branch protection can gate merges on agent reviews/fixes.**
- Diff-view polish + task history.

**Phase 3 — Backend parity**

- **Codex adapter** (`codex exec`, `codex exec resume`, `--json`, `-m/--model`; document the cannot-change-model-on-resume quirk in the UI).
- **Claude Code adapter** (`-p`, `stream-json`, `--resume`).
- Model dropdown per task sourced from `listModels()` for all adapters.
- Optional: shared `opencode serve` backend (attach) to avoid per-run MCP cold boots.

---

## 13. Non-functional requirements

- **Latency:** SSE events render in the UI in near-real-time; console streams without buffering delays. Webhook → task-start latency should be sub-second (validation + dedup only; no slow processing on the webhook path).
- **Reliability:** runs + sessions persisted; interrupted runs resumable; follow-ups work after restart; webhook deliveries **idempotent** (re-delivery never double-runs); check-run conclusions converge to the final task state.
- **Resource safety:** worktree cleanup TTL; cap on concurrent children (== configured concurrency); process kill on abort with timeout then SIGKILL; per-task timeout (default 60 min) prevents runaway agents.
- **Security:** localhost bind; 0600 secrets; PAT never logged/leaked; no secrets interpolated into prompts; optional UI password; **automatic secret masking in logs/console**; webhook signature verification when a secret is configured.
- **Testability:** adapters unit-tested with mocked CLI output; queue tested with fake agents; Octokit interactions mocked (e.g. via `nock`); screening scheduler tested with fake clocks; webhook handler tested with fixture payloads + delivery-id dedup.
- **Portability:** must run on Linux and macOS (dev may build on any machine); document Node version + CLI install requirements per adapter.

---

## 14. Known risks & open questions

1. **CLI output drift** — adapter parsers depend on CLI formats (`opencode --format json`, codex `--json`, claude `stream-json`) that may change across CLI versions. Mitigate: pin documented CLI versions, defensive parsing, show raw lines on parse failure.
2. **Codex resume model lock** — follow-ups on a codex-backed task cannot change the model. Decide in Phase 3: surface as a warning, or auto-fork a fresh run when the user changes the model on a follow-up.
3. **Auto-publish safety** — default auto-publish means tasks push to GitHub unattended. Keep `auto_publish` toggle prominent; consider a per-repo "require approval" override for sensitive repos.
4. **PAT scope limits** — fine-grained PATs must include both Contents and Pull requests scopes for the full flow; validation step in Settings should enumerate exactly which scope is missing.
5. **Concurrency vs. API rate limits** — 4 parallel agents can burn GitHub/LLM rate limits; consider a per-provider throttle later.
6. **Screening false positives** — findings are LLM-generated; severity should be labeled and the "new task from finding" flow should let the user edit the prompt before starting.
7. **Should Jalebi use a GitHub App instead of PAT for higher rate limits & org-install reach?** — deferred, documented as future option.
8. **Webhook reachability** — a localhost app can't receive GitHub webhooks without a tunnel/reverse proxy. Mitigate: detect unreachable webhook, warn in UI, offer per-repo polling fallback; the tunnel is the owner's responsibility (documented in Settings).
9. **Missed/reordered webhook events** — dedup handles re-delivery but not "never delivered"; polling fallback and a "replay delivery" log close the gap for critical triggers (e.g. PR opened).

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

### 16.2 Webhook listener, review automation & check runs

| Project | Repo | Why inspect / what to learn |
|---|---|---|
| **Qodo PR-Agent** (CodiumAI) | `qodo-ai/pr-agent` | Standard for automated PR reviews / issue triage / auto-commenting. Learn webhook event parsing (`pull_request`, `synchronize`, `issue_comment`), structuring multi-file git diffs for LLMs, and inline PR comments via Octokit. |
| **Sweep AI** | `sweepai/sweep` | Early "junior developer" bot for issue→PR workflows. Learn webhook handling, issue parsing, and branch naming (`sweep/...`). |

### 16.3 Git worktree & workspace management

| Project | Repo | Why inspect / what to learn |
|---|---|---|
| **Simple-Git** (Node.js) | `steveukx/simple-git` | Recommended lightweight git client wrapper for the Hono orchestrator. Learn clean `git worktree add/remove`, branch creation, ref fetching, and PAT credential-helper injection without hand-stringing `child_process.exec`. |
| *Search* | git worktree wrappers / lifecycle | Look for `git worktree prune` recovery of orphaned worktrees on app restart. |

### 16.4 UI dashboard & real-time SSE streaming

| Project | Repo | Why inspect / what to learn |
|---|---|---|
| **Open-Canvas** | `langchain-ai/open-canvas` | Open-source document/code editing UI (Next.js/React). Learn side-by-side diff viewers, streaming intermediate agent states, message artifact cards. |
| **react-diff-viewer-continued** | — | Clean side-by-side + unified git diff React component for the diff view. |
| **xterm.js** | — | Standard terminal emulator component for streaming raw stdout/stderr of child-process agent runs. |

---

## 17. Development guidelines & secrets

### 17.1 GitHub token (placeholder)

- Jalebi authenticates to GitHub with a **fine-grained personal access token** supplied by the owner at runtime.
- **Placeholder:** configure it via a local env file the app loads — e.g. `JALEBI_GITHUB_TOKEN=<FINE_GRAINED_PAT_HERE>` in a `.env` (git-ignored), or the Settings UI field, per §F1. A checked-in `.env.example` documents the key name **without** any real value.
- All GitHub calls go through Octokit using that token. Validation at startup must confirm the token and list its granted scopes.

### 17.2 Do not use the `gh` CLI

- **During development of this repository, the `gh` command must NOT be used** for any testing, verification, or other tasks (no `gh auth`, `gh pr`, `gh api`, etc.).
- All GitHub interactions during development/testing must go through the **provided fine-grained token** (via Octokit, `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"`, or git with a credential helper pointing at the token).

### 17.3 Testing repo & account

- The token is for a **testing repository** that is published under a **different account** associated with the current GitHub login (i.e. not the `samosa-ai-com/Gotcha` account/repo).
- Development and testing happen against that dedicated test repo/account only. Do not point any code, tests, or manual checks at production/user repos.
