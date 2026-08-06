# AGENTS.md — Jalebi Development Instructions (ENTRY POINT)

> **Read this file first. It is the mandatory entry point for every agent working on Jalebi.** Everything an agent needs to navigate, behave, and keep updated lives behind this file. Do not skip sections.

---

## 1. What Jalebi is

Jalebi is a **private, self-hosted, localhost-only web application** that behaves like Google's "Jules": a coding-agent dashboard where the owner connects their own GitHub account, creates tasks (fix issue, review PR, implement feature, audit security), watches the agent work through a live step-by-step timeline, comments to give follow-ups, and gets proactively notified of improvement opportunities. It is **event-driven**: repo webhooks can auto-start work in real time (e.g. the moment a PR opens, assigned reviewers begin reviewing automatically).

Key architectural facts:

- **Backend-agnostic agent adapters** — v1 ships the `opencode` CLI; Codex and Claude Code come later. Switching backend = one-line config (`agent.cli`).
- **GitHub PAT** (the owner's own token) drives all GitHub interaction via Octokit. **The `gh` CLI is forbidden.**
- Agents run as **local child processes** in per-task **git worktrees**.
- Stack: Node ≥ 22 + TypeScript + Hono + better-sqlite3 + Drizzle ORM + Octokit + node-cron + React + Vite + Tailwind. SSE for live events. Git CLI (not libgit2).
- Single owner, no multi-user, no cloud, localhost only.

**The authoritative behavioral spec is `JALEBI_PRD.md`.** If anything here or in `docs/` conflicts with the PRD, the PRD wins — flag the conflict, never silently resolve it.

---

## 2. MANDATORY working rules (the contract)

These are non-negotiable. Every agent must follow them on every interaction.

### 2.1 Plan first, always. Never act alone.

1. **Any request** — a feature, a fix, a refactor, a question that implies changes — first produces a **full written plan** covering:
   - Files to be created/modified (exact paths).
   - The exact changes to be made in each file.
   - How the change will be verified (tests, commands, manual checks).
   - Risks/unknowns and any decisions that need the user's input.
2. The plan is presented to the user and the agent **waits for explicit approval**. No exceptions.
3. Only after approval does the agent begin. Work proceeds in **small, verified steps** — one tool use at a time, waiting for results before continuing.
4. The agent **never** writes code, runs commands, edits files, installs packages, or pushes anything without that approved plan.
5. If the scope expands mid-task, stop and re-plan / re-confirm.

### 2.2 Keep the state files updated

- **`HANDOFF.md` is the live development journal.** The agent MUST update it **at the end of every working session** (or after any meaningful milestone) with: current phase, last completed work, files touched, verification results, in-progress work, next steps, and any decisions/blockers.
- **Every behavioral or architectural change must update the matching file under `docs/` in the same change.** No code change ships without its doc update. If a doc does not yet exist for the area, create it and add it to the index in §4.

### 2.3 PRD is the source of truth

- Behavior comes from `JALEBI_PRD.md`. If code, docs, or an instinct disagree with the PRD, **flag the discrepancy to the user** — do not silently pick a side.
- Follow PRD **Goal #10 — Simplicity above all**: no over-engineering, no event-bus frameworks, no complex state machines, no borrowing whole subsystems from reference projects. Borrow only small, specific snippets and re-write them in Jalebi's own style.

### 2.4 Test & verify

- After any code change, run the relevant tests (`npm test` / vitest) and build (`npm run build`) before marking work done.
- Existing tests take precedence: if a change breaks a test, fix the code, not the test (unless the user explicitly asks to change the test).
- Never claim a result without verifying it (exit codes, file checks, test output).

---

## 3. GitHub credentials — THE ONLY token, and nothing else

This is critical and repeated: **there is exactly ONE credential for all GitHub interaction in this project.**

### 3.1 The credential

- **`JALEBI_GITHUB_TOKEN`** — the owner's GitHub personal access token, stored **locally**:
  - While developing: in the git-ignored **`.env`** file at the repo root (see `.env.example` for the key name and required scopes).
  - At runtime: Jalebi moves it into `<data-dir>/secrets.json` with `0600` permissions (PRD §F1) and all GitHub calls go through Octokit using it.

### 3.2 How agents must use it

- **Always authenticate to GitHub using `$JALEBI_GITHUB_TOKEN`** (loaded from `.env` or the app's secrets file). This is the **only** credential to use for: repo list, issues, PRs, reviews, comments, refs, clone/push, webhook registration, check runs, and PAT-scope validation.
- When running git commands that authenticate, use a credential helper or `Authorization: Bearer $JALEBI_GITHUB_TOKEN` — never embed the token in a URL or command that gets logged.

### 3.3 Absolute prohibitions

- **NEVER use the `gh` CLI for testing or verification against the testing repo/account.** All GitHub interaction for testing (repo list, issues, PRs, reviews, comments, refs, clone/push, webhook registration, check runs, PAT-scope validation) goes through **Octokit / `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"` / git with a credential helper** using `JALEBI_GITHUB_TOKEN` — never `gh` (PRD §17.2).
- **Owner-authorized exception (commits only):** the owner has explicitly authorized using the `gh` CLI **only** for local git operations on the Jalebi repo itself (staging, committing, pushing to `Rishabh-Bajpai/Jalebi`). This exception does **not** extend to any testing/verification against the testing account/repo — those always use `JALEBI_GITHUB_TOKEN`.
- **NEVER create, generate, or fall back to any other token or credential** (no `gh auth login`, no new PATs, no OAuth, no alternate tokens). If the token in `.env` is missing/invalid, stop and ask the user — do not improvise.
- **NEVER commit, print, log, or paste the token** — not in code, not in docs, not in AGENTS.md, not in HANDOFF.md, not in any file that could be committed. Token values only ever live in git-ignored files (`.env`, `~/.jalebi/secrets.json`).
- **NEVER pass the token into agent prompts or the console**; Jalebi masks secrets at ingest (PRD §F17).
- If you suspect the token has leaked (was pasted into a committed file, a chat log, etc.), **tell the user immediately** so it can be revoked.

### 3.4 Scope requirements (PRD §F1) — document, don't guess

- Classic PAT: `repo`.
- Fine-grained: Contents read/write, Pull requests read/write, Issues read/write, Metadata read, Commit statuses read/write.

---

## 4. Documentation index & when to update

| File | Purpose | Update when… |
|------|---------|--------------|
| `JALEBI_PRD.md` | Authoritative product spec. Read-only reference. | Never edit it as part of implementation; flag conflicts instead. |
| `HANDOFF.md` | Live dev state: phase, last work, next steps, decisions. | **End of every working session.** |
| `docs/00-overview.md` | Project overview, goals, glossary, how docs are organized. | Anything changes at the overview level. |
| `docs/01-architecture.md` | System architecture, component responsibilities, data flow, SSE. | Architecture/components change. |
| `docs/02-data-model.md` | Full SQLite schema + relationships. | Schema/migrations change. |
| `docs/03-adapters.md` | `AgentAdapter` interface, CLI command refs, parsing, quirks. | Adapter work (opencode/codex/claude). |
| `docs/04-git-workspace.md` | Bare mirrors, worktrees, branch naming, push w/ token. | Git workspace manager changes. |
| `docs/05-github-integration.md` | Octokit client, PAT scopes, webhooks, check runs. | GitHub client/webhook/check-run work. |
| `docs/06-task-queue.md` | Queue, worker pool, run lifecycle, timeouts, retries, publish. | Queue/runner/publish changes. |
| `docs/07-screening.md` | Screening engine, cron, baseline dedup, findings, ntfy. | Screening work. |
| `docs/08-ui.md` | React app structure, pages, components, SSE consumption. | UI changes. |
| `docs/09-testing.md` | Test strategy per layer, how to run tests, fixtures. | Test infra changes. |
| `docs/10-security.md` | Localhost binding, secrets, masking, sandboxing, threat notes. | Security-related changes. |

**If you add a doc file, add it to this table.**

---

## 5. Project map (target structure)

```
Jalebi/
├── AGENTS.md                      ← this file (entry point)
├── HANDOFF.md                     ← live dev state (update every session)
├── JALEBI_PRD.md                  ← authoritative spec
├── .env.example                   ← committed placeholder for env keys
├── .env                           ← git-ignored; holds JALEBI_GITHUB_TOKEN
├── .gitignore
├── docs/                          ← one doc per topic (see §4)
│   ├── 00-overview.md
│   ├── 01-architecture.md
│   ├── 02-data-model.md
│   ├── 03-adapters.md
│   ├── 04-git-workspace.md
│   ├── 05-github-integration.md
│   ├── 06-task-queue.md
│   ├── 07-screening.md
│   ├── 08-ui.md
│   ├── 09-testing.md
│   └── 10-security.md
├── apps/
│   ├── server/                    # Hono orchestrator (Node 22+, TypeScript)
│   │   ├── src/
│   │   │   ├── index.ts           # server bootstrap
│   │   │   ├── config.ts          # env/config loading
│   │   │   ├── db/                # Drizzle schema + migrations
│   │   │   ├── routes/            # REST: tasks, repos, agents, screenings, triggers, settings, webhook
│   │   │   ├── services/          # queue, runner, publish, followup, reviewer, screening, checkrun, masking, artifacts
│   │   │   ├── adapters/          # types.ts, opencode.ts (codex.ts/claude.ts later)
│   │   │   ├── git/               # workspace manager (mirror + worktree + push)
│   │   │   ├── github/            # Octokit client
│   │   │   └── sse/               # event hub
│   │   └── tests/
│   └── web/                       # React + Vite + Tailwind UI
│       └── src/
│           ├── pages/             # Tasks, TaskDetail, Repos, Agents, Screenings, Triggers, Settings
│           ├── components/        # Timeline, Console, DiffViewer, FollowUpComposer, PRCard, …
│           └── api/               # REST + SSE client
└── packages/
    └── shared/                    # shared types + AgentEvent vocabulary
```

---

## 6. Development workflow

1. Install: `npm install` (npm workspaces at the repo root).
2. Copy `.env.example` → `.env` and set `JALEBI_GITHUB_TOKEN` (git-ignored; never commit).
3. Server dev: `npm run dev -w apps/server` (Hono on 127.0.0.1).
4. UI dev: `npm run dev -w apps/web` (Vite).
5. Tests: `npm test` (vitest).
6. Lint/format: `npm run lint` / `npm run format`.
7. Build: `npm run build`.

When a task needs new tooling/dependencies, include that in the plan and get approval before installing.

---

## 7. Roadmap (phases)

Reference `docs/00-overview.md` and `HANDOFF.md` for current phase and status.

- **Phase 0 — Foundation (v1, opencode only):** scaffolding, config, SQLite schema, PAT settings + validation, git workspace manager, `AgentAdapter` + opencode adapter, task queue (concurrency 4), run lifecycle (cancel/timeout/retry), publish (auto/manual, `Closes #N`), secret masking, artifacts, minimal Jules-like UI (queue + task detail + follow-up composer), follow-up via `opencode run --session`.
- **Phase 1 — Catalog & reviewers + event-driven triggers:** catalog agents (personality → `AGENTS.md` injection + skills), reviewer workflow (per-reviewer worktrees + PR review comments), "address reviewers" follow-up, webhook listener + dedup + trigger rules + registration/polling fallback.
- **Phase 2 — Branch control + screening + merge gating:** source/target branch selectors, screening engine (node-cron, HEAD baseline dedup, findings, ntfy, "new task from finding"), check runs for branch protection.
- **Phase 3 — Backend parity:** Codex adapter, Claude Code adapter, per-task model dropdown from `listModels()`.

---

## 8. How to start any task

1. Read this file (§1–§3) and `HANDOFF.md` first.
2. Read the relevant `docs/` file(s) and `JALEBI_PRD.md` sections for the area.
3. Produce a **full plan** (see §2.1) and present it to the user.
4. **Wait for explicit approval** before touching anything.
5. Execute in small verified steps; keep `HANDOFF.md` updated; update `docs/` alongside code.