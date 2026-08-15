# 01 — System Architecture

> **Scope:** System architecture, component responsibilities, data flow, and SSE streaming. Update this file when architecture or components change.

---

## 1. High-level diagram (current implementation)

```
┌────────────────────────── Your machine (127.0.0.1:2052) ──────────────────────────┐
│                    ▲ GitHub webhook events (via tunnel/proxy)                      │
│                    │                                                               │
│  ┌─────────────────┼──────┐  HTTP (REST + SSE)   ┌───────────────────────────────┐ │
│  │  React + Vite UI │      │◀───────────────────▶│  Orchestrator (Flask)          │ │
│  │  (built dist/,   │      │                     │  ┌──────────────────────────┐ │ │
│  │  served by Flask │      │                     │  │ routes/ (blueprints)      │ │ │
│  │  on 2052)        │      │                     │  │  tasks, repos, github,    │ │ │
│  │  queue | task    │      │                     │  │  + SPA fallback           │ │ │
│  │  | console       │      │                     │  └───────────┬──────────────┘ │ │
│  └──────────────────┼──────┘                     │             │                  │ │
│                     │                            │  ┌──────────▼──────────────┐   │ │
│                     │                            │  │ TaskQueue + workers      │   │ │
│                     │                            │  │ (concurrency from         │   │ │
│                     │                            │  │  settings, default 4)     │   │ │
│                     │                            │  └──────────┬──────────────┘   │ │
│                     │                            │             │ events.py (SSE)  │ │
│                     │                            │  ┌──────────▼──────────────┐   │ │
│                     │                            │  │ Agent adapters           │   │ │
│                     │                            │  │  opencode                │   │ │
│                     │                            │  │  codex / claude          │   │ │
│                     │                            │  └──────────┬──────────────┘   │ │
│                     │                            │             │ spawn (cwd=worktree)││
│                     │                            │  ┌──────────▼──────────────┐   │ │
│                     │                            │  │ Git workspace mgr        │   │ │
│                     │                            │  │  bare mirrors +           │   │ │
│                     │                            │  │  per-task worktrees      │   │ │
│                     │                            │  └─────────────────────────┘   │ │
│                     │                            │  SQLite (data.db) + secrets    │ │
│                     │                            │  (Phase 2: scheduler/screening)│ │
│                     │                            └──────────────┬────────────────┘ │
│                     │                                           │ github.py (httpx) │
└─────────────────────┼───────────────────────────────────────────┼──────────────────┘
                      │                                           ▼
                      └─ webhook registration / check runs ─▶ github.com
                                          (fetch refs, push branch, open PR, …)
```

## 2. Component responsibilities

| Component | Responsibility | Status |
|-----------|----------------|--------|
| **UI (React)** | Tasks queue + task detail (timeline, live console, Cancel/Re-run/Publish). Built to `apps/web/dist` and **served by Flask on the same origin (2052)**. Consumes REST; streams SSE via `EventSource`. | implemented |
| **Orchestrator (Flask)** | `create_app` + blueprints (`routes/`): tasks, repos, github, settings, SPA fallback. Task queue + worker pool; run lifecycle; publish; timeout/cancel; masking at ingest; per-task SSE bus (`events.py`). | implemented |
| **Adapters** | Translate a CLI into the `AgentAdapter` interface (`adapters/types.py`): start/resume/list_models/parse. Only component that knows the CLI binary. | opencode implemented |
| **Git workspace mgr** | Bare mirrors (`--bare`, refs under `origin/*`), per-task worktrees (`jalebi/<taskId>`), token-authenticated push via `GIT_CONFIG_*` env. | implemented |
| **GitHub client (httpx)** | Thin httpx client: `validate_token`, `get_repo`, `list_repos`, `create_pr`; PAT-authenticated. | implemented |
| **Webhook listener** | Local endpoint receiving GitHub events; validate + dedup + match trigger rules. | implemented (Phase 1) |
| **Screening scheduler** | Daemon thread (`ScreeningScheduler`) wakes every 60s, matches each screen's 5-field cron (`cron.py`), runs due screens (read-only audits) one at a time with HEAD-baseline dedup + ntfy. | implemented (Phase 2) |
| **Storage** | SQLite via SQLAlchemy 2 + Alembic migrations; `secrets.json` (0600). | implemented |

## 3. Data flow

### 3.1 Manual task (core loop) — implemented

1. User connects a PAT (Settings/`PUT /api/github/token`) → validated, scopes enumerated, stored in `secrets.json` (0600).
2. User creates a task (`POST /api/tasks`) → validated, prompt masked, enqueued as `queued`.
3. A worker picks it up → `GitWorkspace.ensure_mirror` + `create_worktree` (base = `origin/<source>`), then `adapter.start(cwd=worktree, prompt, model)`.
4. Adapter spawns the CLI child process; emits normalized `AgentEvent`s (step/tool_call/message/done/error).
5. Events are **masked at ingest**, broadcast live over the task's SSE channel (`events.py` → `GET /api/tasks/:id/events`), and persisted to `runs.steps_json`.
6. On `done`, if the branch is ahead of `origin/<target>` and `auto_publish`: push branch + open PR (`Closes #N` for `issue_fix`). Publish failure → `needs_approval` (manual `publish`).
7. Terminal status: `done` / `failed` / `timed_out` / `cancelled` / `needs_approval`.

### 3.2 Reviewer loop — planned (Phase 1)

1. A PR exists (from a fix task or external).
2. User assigns 1..N reviewers from the catalog.
3. Each reviewer runs in its **own worktree**, checks out the PR branch, reviews with its own personality/skills/model/CLI, then **posts a PR review comment** via the GitHub API.
4. When all reviewers have posted, the user decides (approve/request changes/merge) — **manually**.
5. Optionally, a follow-up to the original fix agent: "address the reviewers' comments".

### 3.3 Screening loop — implemented (Phase 2)

1. User creates screens per repo (system prompt + cron cadence), from the starter catalog or freeform.
2. `ScreeningScheduler` (daemon thread) wakes every 60s, matches `cadence_cron` via `cron.py`; skips when the repo HEAD is unchanged since the last terminal run (baseline dedup).
3. Runs a **read-only** audit: detached worktree at HEAD, opencode with a "return JSON array" contract; parses findings, masks + stores output, notifies via ntfy when configured.
4. Findings are browsable (`/screenings`); the user converts a finding into a `screen_finding` task — **never auto-started**.

### 3.4 Event-triggered loop (webhook-driven) — planned (Phase 1)

1. GitHub delivers a repo webhook event to the local listener.
2. Listener validates and **idempotently** dedups the delivery (`X-GitHub-Delivery` / `X-GitHub-Event`), then matches against trigger rules.
3. A matching rule creates and enqueues task(s) immediately (e.g. PR opened ⇒ assigned reviewers auto-start).

## 4. SSE streaming

- Backend pushes normalized `AgentEvent`s over a per-task SSE channel: `GET /api/tasks/:id/events` (via the in-process `TaskEvents` bus in `events.py`).
- The stream sends `connected`, then live masked events, then `stream_end` (immediately for already-terminal runs; keepalives every 15s).
- Events are masked at ingest (before broadcast/persist) per PRD §F17 — the UI never needs to mask.

## 5. Stack (decided)

Python 3.13 + Flask + SQLAlchemy 2 (SQLite) + Alembic migrations + httpx (GitHub client) + React + Vite + Tailwind. SSE for events. Git CLI (not libgit2) for repo ops. Managed with `uv` (server) + npm workspaces (web). The backend was switched from the originally planned Node/Hono/Drizzle stack to **Python + Flask + SQLAlchemy + Alembic** (owner decision).

## 6. Key design constraints

- **Backend-agnostic:** the entire system depends on the `AgentAdapter` interface; only the adapter and the `agent_cli` setting know the CLI name (PRD §F4).
- **Simplicity (PRD Goal #10):** no event-bus frameworks, no complex state machines, no distributed abstractions. Borrow only small, specific snippets from reference projects and re-write them in Jalebi's own style.
- **Localhost-only:** server binds to 127.0.0.1 (one port, 2052); optional UI password if exposed via tunnel (PRD §F13).
