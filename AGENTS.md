# AGENTS.md — Jalebi Development Instructions (ENTRY POINT)

> **Read this file first. It is the mandatory entry point for every agent working on Jalebi.** Everything an agent needs to navigate, behave, and keep updated lives behind this file. Do not skip sections.

---

## 1. What Jalebi is

Jalebi is a **private, self-hosted, localhost-only web application** that behaves like Google's "Jules": a coding-agent dashboard where the owner connects their own GitHub account, creates tasks (fix issue, review PR, implement feature, audit security), watches the agent work through a live step-by-step timeline, comments to give follow-ups, and gets proactively notified of improvement opportunities. It is **event-driven**: repo webhooks can auto-start work in real time (e.g. the moment a PR opens, assigned reviewers begin reviewing automatically).

Key architectural facts:

- **Backend-agnostic agent adapters** — opencode, Codex, and Claude Code. The backend + model are chosen **per action** (each task/follow-up/screen/agent form has its own selects); the Settings `default_backend`/`default_model` are only the fallbacks.
- **GitHub PAT** (the owner's own token) drives all GitHub interaction via a thin httpx client. **The `gh` CLI is forbidden.**
- Agents run as **local child processes** in per-task **git worktrees**.
- Stack: Python 3.13 + Flask + SQLAlchemy 2 (SQLite) + Alembic + httpx + React + Vite + Tailwind. SSE for events. Git CLI (not libgit2).
- Single owner, no multi-user, no cloud, localhost only. **One port (2052): Flask serves both the API and the built UI.** (`JALEBI_PORT` overrides.)

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
- **Owner-authorized PRD edits:** the PRD is normally read-only, but the owner has explicitly authorized edits on record — Steps 34–35 (notifications + env vars) and the 2026-08-09 reconciliation (stack/schema/behavior brought in line with the shipped implementation). Any *future* PRD edit still requires explicit owner approval first.

### 2.4 Test & verify

- After any code change, run the relevant tests (`npm test` / vitest) and build (`npm run build`) before marking work done.
- Existing tests take precedence: if a change breaks a test, fix the code, not the test (unless the user explicitly asks to change the test).
- Never claim a result without verifying it (exit codes, file checks, test output).

---

## 3. GitHub credentials — PAT(s), and nothing else

This is critical and repeated: **all GitHub interaction in this project goes through the owner's GitHub PAT(s). There are no other credentials, and the `gh` CLI is never used by Jalebi or its agents.**

### 3.1 The credentials

- **All PATs are equal named accounts.** Every token is stored by name in the `0600` secrets file `<data-dir>/secrets.json` (`github_tokens: [{name, token}]`, added via the GitHub page UI). There is **no primary/default account and no fallback** — the account selected for a task/repo is the one used, and a task without an account is refused.
- **`JALEBI_GITHUB_TOKEN`** is **masking-only** (so a stray value never survives into logs) — it is never used to resolve which account runs anything. While developing/testing it may also be set in the git-ignored **`.env`** file (see `.env.example` for required scopes). All PATs are masked everywhere.

### 3.2 How agents must use it

- **Always authenticate to GitHub using the selected account's PAT** (from the secrets-file vault). This is the **only** credential for: repo list, issues, PRs, reviews, comments, refs, clone/push, webhook registration, check runs, and PAT-scope validation.
- When running git commands that authenticate, use a credential helper or `Authorization: Bearer $JALEBI_GITHUB_TOKEN` — never embed the token in a URL or command that gets logged.
- **The `gh` CLI is banned for agents.** Jalebi hard-blocks it three ways: a worktree `opencode.json` that denies `gh` via opencode permission rules (overrides the global config), an agent environment with no `gh` auth (`GH_CONFIG_DIR` empty, `GH_TOKEN`/`GITHUB_TOKEN` stripped), and an explicit AGENTS.md/prompt instruction. Working git credentials are provided so agents never need to improvise.

### 3.3 Absolute prohibitions

- **NEVER use the `gh` CLI for testing or verification against the testing repo/account.** All GitHub interaction for testing (repo list, issues, PRs, reviews, comments, refs, clone/push, webhook registration, check runs, PAT-scope validation) goes through **the httpx GitHub client / `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"` / git with a credential helper** using `JALEBI_GITHUB_TOKEN` — never `gh` (PRD §17.2).
- **Owner-authorized exception (commits only):** the owner has explicitly authorized using the `gh` CLI **only** for local git operations on the Jalebi repo itself (staging, committing, pushing to `Rishabh-Bajpai/Jalebi`). This exception does **not** extend to any testing/verification against the testing account/repo — those always use `JALEBI_GITHUB_TOKEN`.
- **NEVER create, generate, or fall back to any other token or credential** (no `gh auth login`, no new PATs outside the Jalebi PAT vault, no OAuth, no alternate tokens). If no PAT is configured, stop and ask the user — do not improvise.
- **NEVER commit, print, log, or paste the token** — not in code, not in docs, not in AGENTS.md, not in HANDOFF.md, not in any file that could be committed. Token values only ever live in git-ignored files (`.env`, `~/.jalebi/secrets.json`).
- **NEVER pass the token into agent prompts or the console**; Jalebi masks secrets at ingest (PRD §F17).
- If you suspect the token has leaked (was pasted into a committed file, a chat log, etc.), **tell the user immediately** so it can be revoked.

### 3.4 Scope requirements (PRD §F1) — document, don't guess

- Classic PAT (`repo`) — recommended: `repo` also covers reading GitHub Actions runs/logs (used to inspect a failing workflow and fix it).
- Fine-grained: Contents read/write, Pull requests read/write, Issues read/write, Metadata read, Commit statuses read/write, Actions read (to read failed workflow logs).

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
| `docs/05-github-integration.md` | GitHub client (httpx), PAT scopes, webhooks, check runs. | GitHub client/webhook/check-run work. |
| `docs/06-task-queue.md` | Queue, worker pool, run lifecycle, timeouts, retries, publish. | Queue/runner/publish changes. |
| `docs/07-screening.md` | Screening engine: starter catalog, cron scheduler, baseline dedup, findings JSON, ntfy, UI. | Screening work. |
| `docs/08-ui.md` | React app structure, pages, components, SSE consumption. | UI changes. |
| `docs/09-testing.md` | Test strategy per layer, how to run tests, fixtures. | Test infra changes. |
| `docs/10-security.md` | Localhost binding, secrets, masking, sandboxing, threat notes. | Security-related changes. |
| `docs/11-reliability.md` | Restart recovery, live concurrency, settings live-vs-startup, hardening. | Reliability/ops changes. |
| `docs/12-ui-validation.md` | Manual UI QA checklist for every implemented feature. | Feature/UI behavior changes. |
| `docs/13-phase0-review.md` | Comprehensive Phase 0 code review (logic, security, PRD compliance). | Post-review follow-up changes. |
| `docs/14-env-vars.md` | Env-var store (global + per-repo), `/api/envvars`, `.env` import, agent injection + masking. | Env-var work. |
| `docs/14-messaging-strategy.md` | External messaging templates + invariants (PR body/footer, issue comments, PR reviews, co-author). | Any template/brand change. |
| `docs/15-catalog.md` | Agent catalog (personality → `AGENTS.md`, skills → `@path`, cli/model pins). | Catalog agent work. |
| `docs/16-triggers.md` | Webhook listener, delivery dedup, trigger rules, registration, replay. | Webhook/trigger work. |
| `docs/17-phase1-validation.md` | Manual UI QA checklist for every Phase 1 feature. | Feature/UI behavior changes. |
| `docs/18-cast-workflows-grid.md` | Design-only plan for per-thread cast, reusable workflows, the Inbox-as-helm dashboard, the 4×4 dynamic grid, and future multi-agent orchestration. **Not approved for implementation** — subject to change. | Design discussions only. |
| `docs/19-phase2-validation.md` | Manual UI QA checklist for every Phase 2 feature (screening, commit statuses, diff/history). | Feature/UI behavior changes. |
| `docs/20-phase3-validation.md` | Manual UI QA checklist for every Phase 3 feature (codex/claude backends, per-CLI guards, model dropdown). | Feature/UI behavior changes. |
| `docs/21-phase4-plan.md` | **Approved Phase 4 plan**: visualization/control (T0 agent-waiting UX, T1 hardening, T2 poller+attention, T3 frontend, T4 deps/nudge/durable-SSE/live view, T5 presentation, T6 IDE connector, T7 file browser). | Plan changes during Phase 4 implementation. |
| `docs/22-phase4-validation.md` | Manual UI QA checklist for every Phase 4 feature (T0–T7 + post-plan additions + audit fixes). | Feature/UI behavior changes. |

**If you add a doc file, add it to this table.**

---

## 5. Project map (current structure)

```
Jalebi/
├── AGENTS.md                      ← this file (entry point)
├── HANDOFF.md                     ← live dev state (update every session)
├── JALEBI_PRD.md                  ← authoritative spec
├── .env.example                   ← committed placeholder for env keys
├── .env                           ← git-ignored; holds JALEBI_GITHUB_TOKEN
├── .gitignore
├── package.json                   ← npm workspace (apps/web) + convenience scripts
├── tsconfig.base.json / eslint.config.mjs / .prettierrc.json   ← web tooling
├── start.sh / stop.sh             ← manage the server daemon (PID + log under data dir)
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
│   ├── 10-security.md
│   ├── 11-reliability.md
│   ├── 12-ui-validation.md
│   ├── 13-phase0-review.md
│   ├── 14-env-vars.md
│   ├── 14-messaging-strategy.md
│   ├── 15-catalog.md
│   ├── 16-triggers.md
│   ├── 17-phase1-validation.md
│   ├── 18-cast-workflows-grid.md   ← design-only, NOT approved for implementation
│   ├── 19-phase2-validation.md
│   └── 20-phase3-validation.md
│   ├── 21-phase4-plan.md            ← approved Phase 4 roadmap (+ §16 post-plan additions)
│   └── 22-phase4-validation.md      ← manual QA checklist for Phase 4
├── apps/
│   ├── server/                    # Flask orchestrator (Python 3.13, uv)
│   │   ├── pyproject.toml         # uv project; `jalebi` console script → jalebi.app:main
│   │   ├── alembic.ini
│   │   ├── src/jalebi/
│   │   │   ├── app.py             # create_app factory (+ SPA serving) + CLI entrypoint
│   │   │   ├── config.py          # env → Config (host/port/data_dir/db_url)
│   │   │   ├── db.py              # SQLAlchemy engine/session + Phase-0 models
│   │   │   ├── migrations/        # Alembic env.py + versions/ (current head: c2d3e4f5a6b7)
│   │   │   ├── secrets.py         # 0600 secrets.json (named PAT vault; no primary)
│   │   │   ├── github.py          # thin httpx GitHub client (validate/PR/repos/issues/review)
│   │   │   ├── git_workspace.py   # bare mirrors + worktrees + review worktrees + token-authenticated push
│   │   │   ├── prompts.py         # per-task-type AGENTS.md + follow-up prompt builders
│   │   │   ├── worktree_bootstrap.py  # per-worktree gh-guard (opencode.json) + git identity + AGENTS.md
│   │   │   ├── adapters/          # types.py (AgentEvent/RunHandle) + opencode.py + codex.py + claude.py
│   │   │   ├── events.py          # per-task SSE bus
│   │   │   ├── masking.py         # PAT(s)/pattern redaction at ingest
│   │   │   ├── queue.py           # TaskQueue: workers, run lifecycle, timeout/cancel, publish, review posting
│   │   ├── data_mgmt.py       # usage/backup/prune/vacuum for Settings → Data management
│   │   │   ├── checkruns.py       # check-run registry + lifecycle (PRD F15, merge gating)
│   │   │   ├── repos.py           # connected-repo registry service
│   │   │   ├── tasks.py           # task service (create/list/detail)
│   │   │   ├── screening.py       # ScreeningEngine + ScreeningScheduler (cron, read-only audits)
│   │   │   ├── cron.py            # minimal 5-field cron matcher for the scheduler
│   │   │   └── routes/            # github.py, repos.py, tasks.py, screening.py, data.py (Flask blueprints)
│   │   └── tests/                 # pytest (+ conftest)
│   └── web/                       # React + Vite + Tailwind (built → served by Flask)
│       └── src/
│           ├── pages/             # Tasks, TaskDetail, Screenings
│           ├── api/               # REST + SSE client
│           └── types.ts
```

## 6. Development workflow

1. Server deps: `uv sync` (run inside `apps/server`). Web deps: `npm install` (repo root).
2. Copy `.env.example` → `.env` and set `JALEBI_GITHUB_TOKEN` (git-ignored; never commit).
3. Build the UI once: `npm run build` (output: `apps/web/dist`, served by Flask).
4. Run the app (API + UI on `127.0.0.1:2052`, one port): `./start.sh` — stop with `./stop.sh`.
   (Dev alternative: `uv run --project apps/server jalebi`; optional Vite hot-reload via `npm run dev:web`.)
5. Tests: server `uv run pytest` (in `apps/server`); web `npm test -w @jalebi/web`; or `npm test` for both.
6. Lint/format: `uv run ruff check` (server); `npm run lint` / `npm run format` (web).
7. Build: `npm run build` (web) — the backend has no build step.

When a task needs new tooling/dependencies, include that in the plan and get approval before installing.

---

## 7. Roadmap (phases)

Reference `docs/00-overview.md` and `HANDOFF.md` for current phase and status.

- **Phase 0 — Foundation (v1, opencode only):** scaffolding, config, SQLite schema, PAT settings + validation, git workspace manager, `AgentAdapter` + opencode adapter, task queue (concurrency 4), run lifecycle (cancel/timeout/retry), publish (auto/manual, `Closes #N`), secret masking, artifacts, minimal Jules-like UI (queue + task detail + follow-up composer), follow-up via `opencode run --session`.
- **Phase 1 — Catalog & reviewers + event-driven triggers:** catalog agents (personality → `AGENTS.md` injection + skills), reviewer workflow (per-reviewer worktrees + PR review comments), "address reviewers" follow-up, webhook listener + dedup + trigger rules + registration/polling fallback.
- **Phase 2 — Screening + merge gating:** screening engine (cron, HEAD baseline dedup, findings, ntfy, "new task from finding"), commit statuses for branch protection. **complete.**
- **Phase 3 — Backend parity:** Codex adapter, Claude Code adapter, per-task model dropdown from `listModels()`. **complete.** *(Claude Code unit-tested with captured fixtures; not live-auth'd on the dev machine.)*
- **Phase 4 — Visualization & control:** see **`docs/21-phase4-plan.md`** (+ **`docs/22-phase4-validation.md`** checklist). Agent-"waiting for input" UX + markdown rendering (T0), backend hardening (T1), GitHub polling observer + derived attention (T2), frontend features (T3), deps/nudge/durable-SSE/live running view (T4), presentation fixes (T5), IDE connector (T6), in-task file browser (T7), plus owner-approved extras (§16: PAT rotation, fork-PR flow, attention dismissal, IDE expansion) and pre-PR audit fixes. **Status: 25 commits on `phase-4` ahead of `main` (8 tier commits + post-plan features + 10 audit-fix/docs commits, unpushed); PR pending full gate + `docs/22` manual QA.**

---

## 8. How to start any task

1. Read this file (§1–§3) and `HANDOFF.md` first.
2. Read the relevant `docs/` file(s) and `JALEBI_PRD.md` sections for the area.
3. Produce a **full plan** (see §2.1) and present it to the user.
4. **Wait for explicit approval** before touching anything.
5. Execute in small verified steps; keep `HANDOFF.md` updated; update `docs/` alongside code.