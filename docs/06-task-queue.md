# 06 — Task Queue

> **Scope:** Queue, worker pool, run lifecycle, timeouts, retries, and publish. Update this file for any queue/runner/publish changes.

---

## 1. Overview

Jalebi runs a **task queue** with a **worker pool**. Tasks are persisted in SQLite and survive restarts. The default concurrency is **4** parallel tasks, configurable from the UI (0 = paused queue).

## 2. Concurrency & worker pool (PRD §F3)

- Worker pool executes the configured max number of tasks concurrently; the rest wait in queue.
- Default concurrency: **4**, configurable from the UI (0 = paused queue).
- Cap on concurrent children == configured concurrency (resource safety, PRD §13).

## 3. Task statuses

`queued` → `running` → `waiting_review` / `needs_approval` / `done` / `failed` / `timed_out` / `interrupted`

- **Cancel/abort** a running task: kill child process (for opencode use `POST /session/:id/abort` if attached to a server, else terminate the process).
- Tasks survive restarts (persisted in SQLite); interrupted runs are marked `interrupted` and resumable.

## 4. Run lifecycle

1. Task is created and enqueued (`queued`).
2. A worker picks it up (`running`).
3. Orchestrator creates a worktree, writes personality/skills files, and runs the agent via the adapter (`start`).
4. Adapter spawns the CLI child process; emits normalized `AgentEvent`s over SSE.
5. On completion, orchestrator **publishes** per the publish policy (default auto-open PR).
6. Task reaches a terminal state (`done`/`failed`/`timed_out`/`interrupted`).

## 5. Timeouts (PRD §F16)

- **Per-task timeout (enforced):** default **30 minutes**, configurable per task and per repo.
- On expiry: kill the child process (SIGTERM → SIGKILL), mark the task `failed`/`timed-out`, and complete the check run (if any) with `failure`.

## 6. Retries (PRD §F16)

- A failed/timed-out task can be **re-run** (UI action) — a fresh `run` reusing the same worktree/session where sensible, or a new run when the CLI requires it.
- Retry count is tracked.
- Auto-retry on transient failures (e.g. network) is configurable (default off for publishing tasks, on for pure-review tasks).
- Interrupted runs remain resumable via follow-up.

## 7. Publish (PRD §F9)

- **Default: auto-publish** — on task completion, push the branch and open a PR (auto title = agent summary; body includes task instructions + `Closes #N` when an issue was referenced; footer with a link to the Jalebi task and `Co-authored-by` attribution for opencode).
- **Configurable:** per-task or global `auto_publish: true|false`; when `false`, the UI shows a **"Publish"** button (push + open PR) and a "push-only" option.
- **PR updates on follow-ups:** follow-ups amend the same branch; existing PR is force-updated (new commit pushed) — never a second PR for the same task.

## 8. Follow-ups (PRD §F11)

- Any task with a completed run shows a **follow-up composer**.
- Posting a follow-up → orchestrator calls `adapter.resume({ sessionId, prompt: followup + context })` in the **same worktree**, same branch.
- For a reviewer follow-up or "address the reviewers" request, the prompt includes the current GitHub PR review comments (fetched via API).
- Follow-ups must be **backend-agnostic**.
- Session ids are persisted per run (`runs.session_id`) so follow-ups survive restarts.

## 9. Reviewer orchestration (PRD §F7)

- Each reviewer gets its **own worktree** (isolated clone), checks out the PR's head branch, and runs a review session with its own personality/skills/model/CLI (via the same adapter interface — `start`, not `resume`).
- On completion, the orchestrator posts the reviewer's output as a **PR review comment** on GitHub.
- **Approval is manual** — Jalebi never approves/merges.

## 10. Reference

- PRD §F3 (queue & concurrency), §F7 (reviewers), §F9 (publish), §F11 (follow-ups), §F16 (timeouts & retries).