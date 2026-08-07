# 06 — Task Queue

> **Scope:** Queue, worker pool, run lifecycle, timeouts, retries, and publish. Update this file for any queue/runner/publish changes.

**Implementation:** `src/jalebi/queue.py` (`TaskQueue`), `src/jalebi/tasks.py` (service), `src/jalebi/routes/tasks.py` (API). Tests in `tests/test_queue.py`, `tests/test_api_tasks.py`.

---

## 1. Overview

Jalebi runs a **task queue** with a **worker pool** (threading). Tasks are persisted in SQLite and survive restarts. The default concurrency is **4** (`settings.concurrency`), read at startup; **0 = paused queue**. Concurrency is **live-resizable** (`TaskQueue.set_concurrency`); changing the setting updates the pool without a restart.

## 2. Tasks API (`/api/tasks`)

| Endpoint | Behavior |
|---|---|
| `POST /api/tasks` | `{repo_id, type, prompt, source_branch, target_branch, model, cli, timeout_minutes}` → validate (repo exists, prompt non-empty, type in enum, `cli` supported) → mask prompt (PAT + `secret_patterns`) → create `queued` → enqueue → 201. 400 on invalid. `timeout_minutes` defaults to `settings.default_timeout_minutes`. |
| `GET /api/tasks` | List tasks (newest first) with latest run summary. |
| `GET /api/tasks/:id` | Task detail incl. latest run + steps + followups + artifacts. |
| `POST /api/tasks/:id/cancel` | Queued → `cancelled` immediately; running → kill child → `cancelled`; terminal → 409. |
| `POST /api/tasks/:id/rerun` | Terminal task → back to `queued`, `retry_count+1`, enqueue. 409 if queued/running. |
| `POST /api/tasks/:id/publish` | Manual publish (push + open PR). |
| `POST /api/tasks/:id/followup` | Resume the task's last session (PRD F11). |

## 3. Statuses

`queued → running → done | failed | timed_out | cancelled | needs_approval | interrupted`. `interrupted` is written by **restart recovery** (tasks that were `running` when the process died).

## 4. Run lifecycle (`TaskQueue._run_task`)

Queue items are tagged tuples: `("task", task_id)` or `("followup", task_id, body, pat_name?, model?)`; workers dispatch to `_run_task` / `_run_followup`. The shared execution core is `_prepare_run` (open a `runs` row, flip task `running`) + `_stream_and_finish` (stream masked events → SSE + steps, resolve terminal status, auto-publish unless `publish=False`).

1. Worker dequeues, re-fetches the task; skips if `cancelled` (cancelled-while-queued).
2. **Cancellation state is registered before the `running` commit** so a cancel racing the status flip is never lost.
3. Marks task `running`; creates a `runs` row (`seq` = `max(seq)+1`), recording the task's `pat_name`.
4. `GitWorkspace.ensure_mirror` → `create_worktree` (source branch) → **`worktree_bootstrap.bootstrap_worktree`** writes the worktree's `opencode.json` (denies `gh` via opencode permission rules), sets the git commit identity to `Jalebi <jalebi@localhost>`, and writes `AGENTS.md` (task context + hard rules from `prompts.build_agent_md`) → `adapter.start(cwd=worktree, prompt, model, env)` where `env` carries the owner-PAT git credentials (`GIT_CONFIG_*`), commit identity, `JALEBI_GITHUB_TOKEN`, and strips any `gh` auth (`GH_CONFIG_DIR`, no `GH_TOKEN`/`GITHUB_TOKEN`). The child `pid` is recorded on the run row.
5. Streams `handle.events()`; `step`/`message`/`tool_call`/`done`/`error` events are **masked at ingest** (PRD F17: **all PATs** + `secret_patterns`), broadcast live over the per-task SSE channel, and stored in `runs.steps_json` (bounded: 500 steps, 2000-char texts). **`tool_call` is persisted too**, so a reload doesn't lose console lines. A `cancelled` kill's "exited with code -15" error is replaced with a clean "Run cancelled by user." message.
6. Terminal status from event stream **or** watchdog/cancel reason.
7. If `done` and `settings.auto_publish`: **only if the branch is ahead of `origin/<target>`** (`commits_ahead > 0` — nothing to PR otherwise) → `_publish`: recreate the worktree if it was cleaned, `push_branch`, then **dedup** — if a PR with `head=jalebi/<taskId>` already exists (same-repo, via `find_pr_by_head`) it is **reused** (never a second PR, even if an agent opened one), else `create_pr`. Title/body come from the agent-written `.jalebi/pr.md` when present (`# <title>` + description) — **fallback** to the prompt. Body always gets `Closes #N` (for referenced issues, if not already present) + a Jalebi-task footer + `Co-authored-by`. For `issue_fix`, Jalebi **comments on each referenced issue** linking the PR. On publish failure → task `needs_approval` (manual publish available).
8. Exceptions during the run: the child is killed, `_running` is cleared, the session is rolled back, and the task **and** run are marked `failed` (never left stuck `running`).

### 4a. Task types

- **`freeform`** — the user's text, plus the shared best-practices/hard-rules `AGENTS.md`.
- **`issue_fix`** — task creation fetches the issue (`GET /api/github/context` picker → `github.get_issue`), stores its body in `tasks.context_json` (masked) and the number in `tasks.issues_json`. `AGENTS.md` embeds the issue; the agent implements on the source branch, validates, commits, pushes, and writes `.jalebi/pr.md`. Jalebi opens the PR into the **target** branch (`Closes #N`) and comments on the issue. Requires `issue_number` at creation.
- **`pr_review`** — `TaskQueue._run_review`: the agent runs in a **detached review worktree** checked out at the PR head (`refs/pull/<n>/head`, works for fork PRs too), told to review only (never push). On success Jalebi reads `.jalebi/review.md` (fallback: last message) and posts it as a GitHub PR review **COMMENT** (`POST .../pulls/N/reviews`, event `COMMENT` — never approve/merge). Review tasks never publish. Requires `pr_number` at creation.
- **`screen_finding`** / **`triggered`** — reserved (Phase 2/1).

## 4b. Follow-ups (`TaskQueue._run_followup`, PRD F11)

- `POST /api/tasks/:id/followup` (JSON `{"prompt": ..., "pat_name"?, "model"?}`) requires the task to be **terminal** and a resumable session — the latest run **with a `session_id`** (`tasks.latest_resumable_run`, so a cancelled follow-up run that captured no session falls back to the last good one). The body is masked at ingest; a `followups` row (with the PAT/model override) is recorded against the resumed run.
- The worker resumes with `adapter.resume(cwd=worktree, session_id, prompt + context, env)` — creating a fresh `runs` row (`seq+1`) and streaming live via SSE (watchdog/cancel apply, same as a normal run). PAT/model overrides are honored (defaults: the task's PAT/model).
- **The follow-up runs in the session's own worktree, not the task worktree:** a pr_review session was created in the **review** worktree (`ws/task-<id>-review`, detached at the PR head), so pr_review follow-ups resume there (`create_review_worktree`). Resuming a review session from the task worktree makes opencode's headless `--session` resume return an empty model stream and hang forever (see `docs/03-adapters.md` §6) — the run would sit `running` with an empty timeline until the stall guard or timeout fires.
- On `done` + `auto_publish`: if the branch is ahead it publishes — reusing the task's existing `pr_number` **or** deduping by `head` if a PR already exists, else opening a new PR.
- Cancelling a resumed run kills the child; because a cancelled run may capture no `session_id`, follow-ups fall back to the latest run that has one.

## 5. Timeouts (PRD F16)

- Per-task `timeout_minutes`, defaulting to `settings.default_timeout_minutes` (default 30). A daemon **watchdog thread** enforces it: on expiry it kills the child (SIGTERM → 5s grace → SIGKILL) and the run resolves to `timed_out`. A timeout of `0` fires immediately (used in tests).
- **Stall guard:** a second daemon **stall watchdog** (`STALL_TIMEOUT_SECONDS = 120`) kills the child if the agent process stays alive but emits **no event for 120s** (checked from run start). The run then resolves to `failed` with a diagnostic step ("Agent produced no output for 120s — the agent process hung and was terminated"). This bounds the empty-stream/hang failure mode so no run can sit `running` with an empty timeline indefinitely; the total-budget timeout remains the last line of defence.

## 6. Cancellation (PRD F3)

- `cancel()` sets a reason and kills the child process (SIGTERM → SIGKILL); the event stream ends and the run resolves to `cancelled`. Cancellation works for a task that is `queued` (status flip only) or `running` (child killed).

## 7. Retries (PRD F16)

- `rerun` reuses the same task row + worktree (`create_worktree` resumes an existing worktree); a fresh agent session runs (new `runs` row, new `seq`). `retry_count` is tracked.
- **Auto-retry:** if `settings.retry_policy.auto_retry` is set, a run that ends `failed` is re-enqueued once (`retry_count` capped at 1) as a fresh run.

## 8. Restart recovery (PRD F3/F13)

- On startup, `TaskQueue.recover()` (called from `main()`) marks every `running` run **and** task `interrupted`, kills orphaned agent processes using the recorded `runs.pid`, and re-enqueues tasks that were still `queued`. `queued` tasks therefore survive a restart; `running` tasks are resumable via `rerun`.

## 9. Live events (SSE)

- `GET /api/tasks/:id/events` streams the run's masked events (`connected` → live `step`/`message`/`tool_call`/`done`/`error` → `stream_end`), via the in-process `TaskEvents` bus (`src/jalebi/events.py`). The stream closes when the run ends (or immediately for already-terminal runs). The UI leaves the `EventSource` open on transient errors so the browser auto-reconnects.

## 10. Known limitations (flagged)

- No per-task `auto_publish` override (global setting only). A cancelled/killed agent session may become unresumable (opencode-side session state).
- Worktree TTL cleanup (deleting `done` task worktrees after N days) is not yet implemented (only artifacts are pruned).
- The `gh` denial is enforced at the opencode permission layer + env hygiene; a hypothetical full-path `/usr/bin/gh` invocation inside a compound command could in theory reach a shell, but with no `gh` credentials in the env it cannot act as the owner.

## 11. Artifacts (PRD F18)

- After a run reaches a terminal state, the **untracked, non-ignored** files in its worktree are copied to `<data-dir>/artifacts/<run_id>/` (store) and recorded as `artifacts` rows + `runs.artifacts_json` (cache of refs). Committed code and git-ignored files (e.g. `node_modules`) are skipped.
- Retention: `settings.artifact_ttl_days` (default 7). `artifacts.prune_artifacts` deletes expired rows + stored files and runs once at server startup.
- Download: `GET /api/tasks/<task_id>/artifacts/<artifact_id>/download` (path-traversal-safe).
- Captures run for every terminal outcome (`done`/`failed`/`timed_out`/…) and for follow-up runs too.

## 12. Reference

- PRD §F3 (queue & concurrency), §F9 (publish), §F16 (timeouts & retries), §F17 (masking), §F18 (artifacts).
