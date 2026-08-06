# 06 — Task Queue

> **Scope:** Queue, worker pool, run lifecycle, timeouts, retries, and publish. Update this file for any queue/runner/publish changes.

**Implementation:** `src/jalebi/queue.py` (`TaskQueue`), `src/jalebi/tasks.py` (service), `src/jalebi/routes/tasks.py` (API). Tests in `tests/test_queue.py`, `tests/test_api_tasks.py`.

---

## 1. Overview

Jalebi runs a **task queue** with a **worker pool** (threading). Tasks are persisted in SQLite and survive restarts. The default concurrency is **4** (`settings.concurrency`), read at startup; **0 = paused queue** (no workers spawned).

## 2. Tasks API (`/api/tasks`)

| Endpoint | Behavior |
|---|---|
| `POST /api/tasks` | `{repo_id, type, prompt, source_branch, target_branch, model, cli, timeout_minutes}` → validate (repo exists, prompt non-empty, type in enum) → mask prompt → create `queued` → enqueue → 201. 400 on invalid. |
| `GET /api/tasks` | List tasks (newest first) with latest run summary. |
| `GET /api/tasks/:id` | Task detail incl. latest run + steps. |
| `POST /api/tasks/:id/cancel` | Queued → `cancelled` immediately; running → kill child → `cancelled`; terminal → 409. |
| `POST /api/tasks/:id/rerun` | Terminal task → back to `queued`, `retry_count+1`, enqueue. 409 if queued/running. |
| `POST /api/tasks/:id/publish` | Manual publish (push + open PR). |

## 3. Statuses

`queued → running → done | failed | timed_out | cancelled | needs_approval` (plus `interrupted` reserved for future restart recovery).

## 4. Run lifecycle (`TaskQueue._run_task`)

1. Worker dequeues `task_id`, re-fetches the task; skips if `cancelled` (cancelled-while-queued).
2. Marks task `running`; creates a `runs` row (`seq+1`).
3. `GitWorkspace.ensure_mirror` → `create_worktree` (source branch) → `adapter.start(cwd=worktree, prompt, model)`.
4. Streams `handle.events()`; `step`/`message`/`done`/`error` events are **masked at ingest** (PRD F17: PAT + `secret_patterns`) and stored in `runs.steps_json` (bounded: 500 steps, 2000-char texts).
5. Terminal status from event stream **or** watchdog/cancel reason.
6. If `done` and `settings.auto_publish`: `push_branch` + `GitHubClient.create_pr` (`head=jalebi/<taskId>`, `base=target_branch`, title `[Jalebi] <first prompt line>`, body includes prompt + `Closes #N` for `issue_fix`). On publish failure → task `needs_approval` (manual publish available).
7. Exceptions during the run mark the task **and** the current run `failed`.

## 5. Timeouts (PRD F16)

- Per-task `timeout_minutes` (default 30). A daemon **watchdog thread** enforces it: on expiry it kills the child (SIGTERM → 5s grace → SIGKILL) and the run resolves to `timed_out`. A timeout of `0` fires immediately (used in tests).

## 6. Cancellation (PRD F3)

- `cancel()` sets a reason and kills the child process (SIGTERM → SIGKILL); the event stream ends and the run resolves to `cancelled`.

## 7. Retries (PRD F16)

- `rerun` reuses the same task row + worktree (`create_worktree` resumes an existing worktree); a fresh agent session runs (new `runs` row, new `seq`). `retry_count` is tracked.
- Auto-retry on transient failures is not yet implemented.

## 8. Publish (PRD F9)

- **Default auto-publish** (`settings.auto_publish`), global setting (per-task override not yet implemented).
- Manual path: `POST /api/tasks/:id/publish`.
- Follow-ups/PR-update semantics arrive with the follow-up feature.

## 9. Known limitations (flagged)

- Worker pool size is fixed at **startup** from `settings.concurrency`; changing the setting requires a restart.
- `steps_json` is written at run completion (no live streaming yet — SSE lands with the UI step).
- No artifact capture yet (PRD F18); no restart-recovery/`interrupted` handling yet.

## 10. Reference

- PRD §F3 (queue & concurrency), §F9 (publish), §F16 (timeouts & retries), §F17 (masking).
