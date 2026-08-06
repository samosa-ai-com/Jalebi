# 11 — Reliability & Operations

> **Scope:** Crash recovery, live queue control, which settings take effect immediately, and the hardening guarantees of Phase 0. Update this file for any reliability/ops change.

---

## 1. Restart recovery (PRD §F3, §F13)

The task queue is in-memory; on a process crash the following would otherwise be lost:

- tasks stuck `running` (the DB says running but no worker is executing them),
- tasks `queued` but never dequeued,
- orphaned `opencode` child processes still editing worktrees.

At startup `main()` calls `TaskQueue.recover()`, which:

1. kills any orphaned agent process using the **`runs.pid`** recorded when the run started,
2. marks every `running` run **and** task `interrupted` (`finished_at` set),
3. re-enqueues every task that was still `queued` (so they run once workers start).

`interrupted` tasks are resumable via **Re-run** (fresh session) or **Follow-up** (if a session id survived).

## 2. Live concurrency control (PRD §F3)

- `TaskQueue.set_concurrency(n)` resizes the worker pool at runtime:
  - `n > current` → spawns additional workers.
  - `n < current` → surplus workers drain the queue for a `None` sentinel and exit.
  - `n == 0` → **pauses** the queue (workers idle; nothing is dequeued until `n > 0`).
- `POST /api/settings {key: "concurrency", value: n}` applies this immediately (no restart).

## 3. Settings: live vs startup

| Setting | Applies |
|---|---|
| `concurrency` | **live** (pool resizes immediately) |
| `auto_publish` | live (checked per run) |
| `agent_cli` | live (per run) |
| `secret_patterns` | live (per run/prompt ingest) |
| `default_timeout_minutes` | live (used for new tasks / watchdog fallback) |
| `retry_policy.auto_retry` | live (per run) |
| `artifact_ttl_days` | **startup only** (artifact prune runs once at boot) |
| `ntfy_topic` | reserved for Phase 2 (screening notifications) |

Settings values are **type-validated** on `POST /api/settings` (rejects `"false"` for a bool, non-integers for numbers, non-list `secret_patterns`, unsupported `agent_cli`). Only the `opencode` CLI is currently supported.

## 4. Security hardening

- Prompts, follow-up bodies, and PR title/body are masked with the **PAT and `secret_patterns`** at ingest — pattern-secrets never reach GitHub PRs.
- Unknown `/api/*` paths return `404` JSON (they do not fall through to the SPA `index.html`).
- Artifact downloads are path-traversal-safe; the PAT never appears in argv, URLs, or logs (git auth via `GIT_CONFIG_*` Basic header).

## 5. Known limitations (flagged)

- Worktree TTL cleanup (deleting `done` task worktrees after N days) is **not implemented** — only artifacts are pruned.
- No per-task `auto_publish` override (global setting only).
- A cancelled/killed agent session may become unresumable (opencode-side session state); the task is still marked `cancelled`/`interrupted`.
- Single-port localhost only — no TLS, no auth (by design; PRD §F13).

## 6. Reference

- PRD §F3 (queue & concurrency), §F9 (publish), §F13 (security), §F16 (timeouts & retries), §F17 (masking), §F18 (artifacts).
- Implementation: `src/jalebi/queue.py` (`recover`, `set_concurrency`, `_maybe_retry`, `_kill_pid`), `src/jalebi/app.py` (`main`, settings validation), `src/jalebi/routes/tasks.py` (default timeout, masking). Tests in `tests/test_reliability.py`.
