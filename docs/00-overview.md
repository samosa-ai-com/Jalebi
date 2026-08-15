# 00 — Project Overview

> **Scope:** What Jalebi is, its goals, glossary, and how this documentation set is organized. Update this file when anything changes at the overview level.

---

## 1. What Jalebi is

Jalebi is a **private, self-hosted, localhost-only web application** that behaves like Google's "Jules": a coding-agent dashboard where the owner connects their own GitHub account, creates tasks (fix issue, review PR, implement feature, audit security), watches the agent work through a live step-by-step timeline, comments to give follow-ups, and gets proactively notified of improvement opportunities. It is **event-driven**: repo webhooks can auto-start work in real time.

The authoritative behavioral spec is **`JALEBI_PRD.md`** at the repo root. This doc set is a navigable, per-topic companion to the PRD — it never overrides it.

**Stack (as implemented):** Python 3.13 + Flask + SQLAlchemy 2 (SQLite) + Alembic + httpx (GitHub client) on the server, managed with `uv`; React + Vite + Tailwind on the web. The built UI is served by Flask on the **same port as the API (default 2052)** — a single origin. Git CLI (not libgit2) for repo ops.

## 2. Core principles

1. **Jules-like experience, self-hosted** — dedicated web UI with a task queue, per-task timeline, live logs, incremental diff, and follow-up panel. Owner-only.
2. **Pluggable agent backends** — v1 ships `opencode`; Codex and Claude Code come later. Switching backend = one-line config (`agent.cli`).
3. **Model freedom** — any model available to the selected CLI, per task and per agent.
4. **Agent catalog** — named agents = personality (markdown → `AGENTS.md`) + skills + optional model + optional CLI.
5. **Reviewer workflow** — catalog reviewers run in their own worktrees and post PR review comments.
6. **Branch control** — per task type: `issue_fix` single target branch, `freeform` source/target, `pr_review` none.
7. **Proactive screening** — scheduled, notify-only audits (never auto-act).
8. **Privacy & ownership** — localhost-bound, PAT-authenticated, single-owner.
9. **Event-driven automation** — webhook-pushed triggers (not polling).
10. **Simplicity above all** — no over-engineering (PRD Goal #10).

## 3. Non-goals (v1)

- No slash-command triggers from GitHub comments.
- No multi-user accounts, roles, or team collaboration.
- No cloud hosting; localhost only.
- No GitHub App / OAuth — PAT only.
- No auto-acting screenings.
- No Gemini CLI adapter (replaced by Codex).
- Jalebi is independent of the existing Chanakya automation in the Gotcha repo.

## 4. Glossary

| Term | Meaning |
|------|---------|
| **Task** | A unit of work in Jalebi (fix issue, review PR, free-text instruction, screen). |
| **Run** | One agent execution (one CLI child process) within a task. |
| **Follow-up** | A user comment on a task that resumes the same agent session. |
| **Adapter** | A backend integration mapping a CLI (opencode/Codex/Claude) to one common interface. |
| **Catalog agent** | A user-configured named agent = personality + skills + optional model + optional CLI. |
| **Reviewer** | A catalog agent of kind `reviewer` assigned to a PR. |
| **Screen/Screening** | A scheduled proactive audit with its own system prompt + cadence. |
| **Webhook trigger** | A repo webhook event that auto-starts task(s) per user rules. |
| **Check run** | A GitHub commit status reporting a task's state — can gate merges via branch protection. |
| **Artifact** | A file produced by a run (log, report, coverage) captured per run. |
| **Worktree** | A git worktree — an isolated checkout of a repo for a single task/agent. |
| **Publish** | Push branch + open (or update) a PR. |
| **Resume** | Continue an existing agent session (CLI-native continuation). |

## 5. How this documentation set is organized

| Doc | Topic |
|-----|-------|
| `00-overview.md` | This file — overview, goals, glossary, organization. |
| `01-architecture.md` | System architecture, components, data flow, SSE. |
| `02-data-model.md` | Full SQLite schema + relationships. |
| `03-adapters.md` | `AgentAdapter` interface, CLI refs, parsing, quirks. |
| `04-git-workspace.md` | Bare mirrors, worktrees, branch naming, push w/ token. |
| `05-github-integration.md` | GitHub client (httpx), PAT scopes, webhooks, check runs. |
| `06-task-queue.md` | Queue, worker pool, run lifecycle, timeouts, retries, publish. |
| `07-screening.md` | Screening engine, cron, baseline dedup, findings, ntfy. |
| `08-ui.md` | React app structure, pages, components, SSE consumption. |
| `09-testing.md` | Test strategy per layer, how to run tests, fixtures. |
| `10-security.md` | Localhost binding, secrets, masking, sandboxing, threat notes. |
| `11-reliability.md` | Restart recovery, live concurrency, settings live-vs-startup, hardening. |
| `12-ui-validation.md` | Manual UI QA checklist for every implemented feature. |
| `13-phase0-review.md` | Comprehensive Phase 0 code review (logic, security, PRD compliance). |
| `14-env-vars.md` | Env-var store (global + per-repo), `/api/envvars`, `.env` import. |
| `14-messaging-strategy.md` | External messaging templates + invariants (PR body/footer, issue comments, PR reviews). |
| `15-catalog.md` | Agent catalog (personality → `AGENTS.md`, skills → `@path`). |
| `16-triggers.md` | Webhook listener, trigger rules, deliveries, registration, replay. |
| `17-phase1-validation.md` | Manual UI QA checklist for every Phase 1 feature. |
| `18-cast-workflows-grid.md` | Design-only plan for the Inbox-as-helm grid dashboard — **not approved** for implementation. |
| `19-phase2-validation.md` | Manual UI QA checklist for every Phase 2 feature (screening, commit statuses, diff/history). |

## 6. Roadmap (phases)

- **Phase 0 — Foundation (v1, opencode only):** scaffolding, config, SQLite schema, PAT settings + validation, git workspace manager, `AgentAdapter` + opencode adapter, task queue (concurrency 4), run lifecycle (cancel/timeout/retry), publish (auto/manual, `Closes #N`), secret masking, minimal Jules-like UI (queue + task detail + live console), follow-up via `opencode run --session`, artifacts (F18), restart recovery + live concurrency. **Status: complete (hardened).**
- **Phase 1 — Catalog & reviewers + event-driven triggers:** catalog agents, reviewer workflow, "address reviewers" follow-up, webhook listener + dedup + trigger rules + registration. **Status: complete (polling fallback deferred/inert).**
- **Phase 2 — Screening + merge gating:** screening engine, commit statuses for branch protection, diff-view polish + task history. **Status: complete.**
- **Phase 3 — Backend parity:** Codex adapter, Claude Code adapter, per-task model dropdown from `listModels()`.

See `HANDOFF.md` for the current phase and live status.