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

**Persistence:** every setting is stored as a row in the SQLite `settings` table
(JSON-encoded values, written with an explicit commit) — nothing is held in
memory. `settings.seed_defaults()` runs at **app startup** (`create_app`) and
inserts a row for every default that has no stored value yet, so all
configurations are explicit, inspectable, and survive restarts. User-modified
values are **never overwritten** by seeding. `get_setting` falls back to the code
default only for a key added by a code update before the next restart.

| Setting | Applies |
|---|---|
| `concurrency` | **live** (pool resizes immediately) |
| `auto_publish` | live (checked per run) |
| `agent_cli` | live (per run) |
| `secret_patterns` | live (per run/prompt ingest) |
| `default_timeout_minutes` | live (used for new tasks / watchdog fallback) |
| `retry_policy` (`auto_retry`, `continue_prompt`, `timeout_multiplier`, `max_timeout_minutes`) | live (per run completion) |
| `stall_timeout_seconds` | live (per run start) |
| `artifact_ttl_days` | **startup only** (artifact prune runs once at boot) |
| `ntfy_topic` | live (per notification; merged endpoint — bare topic or full URL) |
| `notify_on_done` / `notify_on_failed` / `notify_on_progress` / `notify_on_needs_approval` | live (per run/progress ping) |
| `notify_progress_interval_minutes` | live (progress watchdog reads it each loop) |
| `timezone` | **live** (`jalebi/clock.set_zone` on save — screening cron matching + all timestamps follow it immediately; column defaults use a module-global zone synced at startup) |

Settings values are **type-validated** on `POST /api/settings` (rejects `"false"` for a bool, non-integers for numbers, non-list `secret_patterns`, unsupported `agent_cli`); `secret_patterns` must be compilable regexes; `ntfy_topic` must be empty, a bare topic, or an `http(s)://` URL. Only the `opencode` CLI is currently supported.

## 4. Security hardening

- Prompts, follow-up bodies, PR title/body, run-end diffs, and artifacts are masked with the **PAT and `secret_patterns`** at ingest — pattern-secrets never reach GitHub PRs.
- Unknown `/api/*` paths return `404` JSON (they do not fall through to the SPA `index.html`).
- Artifact downloads are path-traversal-safe; the PAT never appears in argv, URLs, or logs (git auth via `GIT_CONFIG_*` Basic header). Agent children spawn in their own session and cancel/timeout kill the whole process group.
- **Stall guard** (`settings.stall_timeout_seconds`, default 600s) bounds the empty-stream/hang failure mode — a quiet-but-healthy sub-agent/tool phase gets a generous window, and if it still trips, **auto-recovery** (see `docs/06-task-queue.md` §7) resumes/restarts the run instead of stranding the task. The per-task timeout remains the last line of defence.

## 5. Known limitations (flagged)

- Worktree TTL cleanup (deleting `done` task worktrees after N days) is **not implemented** — only artifacts are pruned.
- No per-task `auto_publish` override (global setting only).
- A cancelled/killed agent session may become unresumable (opencode-side session state); the task is still marked `cancelled`/`interrupted`.
- Single-port localhost only — no TLS; optional Basic-auth UI password when exposed (PRD §F13).
- **Screening scheduler** is a daemon thread (dies with the process). A run in flight when the server stops is left `running` in the DB; on restart it has no live HEAD comparison until the next cron tick, and a manual "Run now" starts fresh (screening runs have no resume). Runs are persisted and never lost.
- **Commit statuses** (PRD F15) are best-effort: a status API failure never fails the task, but a failed status leaves the GitHub-side status `pending` until the next run/publish for that head reconciles it. Follow-ups replace the existing status (matched by `(sha, context)`).

## 6. Reference

- PRD §F3 (queue & concurrency), §F9 (publish), §F13 (security), §F16 (timeouts & retries), §F17 (masking), §F18 (artifacts).
- Implementation: `src/jalebi/queue.py` (`recover`, `set_concurrency`, `_maybe_retry`, `_kill_pid`), `src/jalebi/app.py` (`main`, settings validation), `src/jalebi/routes/tasks.py` (default timeout, masking). Tests in `tests/test_reliability.py`.
