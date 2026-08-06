# 01 — System Architecture

> **Scope:** System architecture, component responsibilities, data flow, and SSE streaming. Update this file when architecture or components change.

---

## 1. High-level diagram

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

## 2. Component responsibilities

| Component | Responsibility |
|-----------|----------------|
| **UI (React)** | Queue, task detail (timeline/logs/diff/follow-ups), agents catalog editor, screenings config, triggers, settings. Consumes REST; subscribes to SSE event stream. |
| **Orchestrator (Hono)** | REST + SSE; task queue + worker pool; run lifecycle; publish; follow-up dispatch; reviewer orchestration; baseline bookkeeping; webhook handling (validate + dedup + match rules); check-run lifecycle; timeouts/retries; secret masking; artifact capture. |
| **Adapters** | Translate a CLI into the `AgentAdapter` interface (start/resume/listModels/parse). Only component that knows the CLI binary. |
| **Git workspace mgr** | Bare mirrors, worktree create/discard, branch naming (`jalebi/<taskId>`), push with token credential helper, ref-prefetch for screenings. |
| **GitHub client (Octokit)** | Issues, PRs, reviews, comments, refs, webhook registration, check runs; all PAT-authenticated. |
| **Webhook listener** | Local endpoint receiving GitHub events (optionally tunneled/reverse-proxied); forwards validated deliveries to the orchestrator. |
| **Scheduler** | Cron screening runs + dedup + notifications. (Triggering is webhook-driven, NOT scheduler-driven.) |
| **Storage** | SQLite schema; secrets file; artifact store. |

## 3. Data flow

### 3.1 Manual task (core loop)

1. User connects a PAT (Settings) → validated, scopes enumerated, stored in `secrets.json` (0600).
2. User creates a task: repo, type, source/target branches, catalog agent (or default), model, instructions.
3. Orchestrator creates a **worktree**, writes personality/skills files, runs the agent via the adapter (`start`).
4. Adapter spawns the CLI child process in the worktree; emits normalized `AgentEvent`s.
5. SSE hub broadcasts events to the task's channel; UI renders timeline/logs/diff live.
6. On completion, orchestrator **publishes** (auto by default) → push + open PR.
7. User posts a **follow-up** → orchestrator calls `adapter.resume` in the same worktree → branch/PR updated.

### 3.2 Reviewer loop

1. A PR exists (from a fix task or external).
2. User assigns 1..N reviewers from the catalog.
3. Each reviewer runs in its **own worktree**, checks out the PR branch, reviews with its own personality/skills/model/CLI, validates, then **posts a PR review comment** via the GitHub API.
4. When all reviewers have posted, the user decides (approve/request changes/merge) — **manually**.
5. Optionally, a follow-up to the original fix agent: "address the reviewers' comments" — the fixer resumes, fetches PR comments, updates the PR.

### 3.3 Screening loop

1. User enables/creates screenings per repo (system prompt + cron cadence).
2. Scheduler checks cadence; skips if repo HEAD unchanged since last run (baseline dedup).
3. Runs a read-only audit; parses findings; stores them; notifies (in-app + optional ntfy).
4. Findings are browseable; user can convert a finding into a task — **never auto-started**.

### 3.4 Event-triggered loop (webhook-driven)

1. GitHub delivers a repo webhook event to the local listener.
2. Listener validates and **idempotently** dedups the delivery (`X-GitHub-Delivery` / `X-GitHub-Event`), then matches against trigger rules.
3. A matching rule creates and enqueues task(s) immediately (e.g. PR opened ⇒ assigned reviewers auto-start).
4. Runs proceed like manual tasks and report back via **check runs** on the PR head commit when configured.
5. No polling involved; a manual "replay last event" button and an optional polling fallback exist.

## 4. SSE streaming

- Backend pushes normalized `AgentEvent`s over a per-task SSE channel: `GET /api/tasks/:id/events`.
- The UI renders timeline/logs/diffs live from this stream.
- Events are masked at ingest (before broadcast/persist) per PRD §F17.

## 5. Recommended stack

Python 3.13 + Flask + SQLAlchemy 2 (SQLite) + Alembic migrations + PyGithub-equivalent Octokit-style client + APScheduler-equivalent cron + React + Vite + Tailwind. SSE for events. Git CLI (not libgit2) for repo ops.

> **Note (2026-08-06):** the backend was switched from the originally planned Node/Hono/Drizzle stack to **Python + Flask + SQLAlchemy + Alembic** (owner decision). This doc's architecture diagram and component table still reference the Node-based layout; they are **reference-only** and will be rewritten in a future doc-sync pass.

## 6. Key design constraints

- **Backend-agnostic:** the entire system depends on the `AgentAdapter` interface; only the adapter and a one-line config know the CLI name (PRD §F4).
- **Simplicity (PRD Goal #10):** no event-bus frameworks, no complex state machines, no distributed abstractions. Borrow only small, specific snippets from reference projects and re-write them in Jalebi's own style.
- **Localhost-only:** server binds to 127.0.0.1; optional UI password if exposed via tunnel (PRD §F13).