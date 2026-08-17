# 06 — Task Queue

> **Scope:** Queue, worker pool, run lifecycle, timeouts, retries, and publish. Update this file for any queue/runner/publish changes.

**Implementation:** `src/jalebi/queue.py` (`TaskQueue`), `src/jalebi/tasks.py` (service), `src/jalebi/routes/tasks.py` (API). Tests in `tests/test_queue.py`, `tests/test_api_tasks.py`.

---

## 1. Overview

Jalebi runs a **task queue** with a **worker pool** (threading). Tasks are persisted in SQLite and survive restarts. The default concurrency is **4** (`settings.concurrency`), read at startup; **0 = paused queue**. Concurrency is **live-resizable** (`TaskQueue.set_concurrency`); changing the setting updates the pool without a restart.

## 2. Tasks API (`/api/tasks`)

| Endpoint | Behavior |
|---|---|
| `POST /api/tasks` | `{repo_id, type, prompt, source_branch, target_branch, model, cli, timeout_minutes}` → validate (repo exists, prompt non-empty, type in enum, `cli` supported) → mask prompt (PAT + `secret_patterns`) → create `queued` → enqueue → 201. 400 on invalid. `timeout_minutes` defaults to `settings.default_timeout_minutes`. `source_branch` is ignored for `issue_fix` (single-target model). |
| `GET /api/tasks` | List tasks (newest first) with latest run summary. |
| `GET /api/tasks/:id` | Task detail incl. latest run + steps + followups + artifacts. |
| `POST /api/tasks/:id/cancel` | Queued → `cancelled` immediately; running → kill child → `cancelled`; terminal → 409. |
| `POST /api/tasks/:id/rerun` | Terminal task → back to `queued` and enqueued. 409 if queued/running. Does **not** bump `retry_count` (that counter tracks auto-retries only, so a manual rerun never consumes the auto-retry budget). |
| `POST /api/tasks/:id/publish` | Manual publish. JSON body `{mode, branch?, pr_number?}` (all optional). `mode` ∈ `{new_pr, update_pr, push_branch}`; defaults to `new_pr` for backwards compat (empty body still works). |
| `POST /api/tasks/:id/followup` | Resume the task's last session (PRD F11). |

## 3. Statuses

`queued → running → done | failed | timed_out | cancelled | needs_approval | interrupted`. `interrupted` is written by **restart recovery** (tasks that were `running` when the process died).

## 4. Run lifecycle (`TaskQueue._run_task`)

Queue items are tagged tuples: `("task", task_id)` or `("followup", task_id, body, pat_name?, model?)`; workers dispatch to `_run_task` / `_run_followup`. The shared execution core is `_prepare_run` (open a `runs` row, flip task `running`) + `_stream_and_finish` (stream masked events → SSE + steps, resolve terminal status, auto-publish unless `publish=False`).

1. Worker dequeues, re-fetches the task; skips if `cancelled` (cancelled-while-queued).
2. **Cancellation state is registered before the `running` commit** so a cancel racing the status flip is never lost; after registering, the worker **re-reads the task status** (the first snapshot may be stale) and bails if a queued-cancel landed mid-pickup. The queued-cancel route also calls `queue.cancel` so an already-registered pickup is flagged too.
3. Marks task `running`; creates a `runs` row (`seq` = `max(seq)+1`), recording the task's `pat_name`.
4. `GitWorkspace.ensure_mirror` → `create_worktree` (base branch — **`issue_fix` uses the target/PR-base branch (single-target model), other types the source branch**; the mirror's worktree registrations are **pruned** first so a task id whose worktree dir was deleted without being unregistered — e.g. task-delete cleanup — never fails `worktree add`; the **first run of a task resets any stale `jalebi/<taskId>` branch** from a wiped/restored DB back to the current `origin/<base>` — reruns/follow-ups resume) → **`worktree_bootstrap.bootstrap_worktree`** writes the worktree's `opencode.json` (denies `gh` via opencode permission rules **and sets `permission.external_directory: "deny"`** so the agent's file tools and path-bearing bash commands cannot touch anything outside the worktree — this overrides the owner's global `external_directory: "allow"`), sets the git commit identity to `Jalebi <jalebi@localhost>`, and writes `AGENTS.md` (task context + hard rules from `prompts.build_agent_md`) → `adapter.start(cwd=worktree, prompt, model, env)`. **Catalog agent resolution (PRD F6):** before starting, the worker resolves the task's `agent_id` (`_catalog_agent` — re-validates exists + enabled; a deleted/disabled-after-creation agent falls back to the default build agent rather than failing) and applies its **pinned `cli`/`model`** — precedence is **explicit task override > live agent pin > default** for both (`_agent_run_opts` / `effective_model`), so editing an agent re-points any task that didn't override — **appends `custom_instructions`** to the prompt, **merges `personality_md` + `@path` skill links** into `AGENTS.md` (`prompts.build_agent_md(task, repo, agent=...)`), and **materializes skills** into `.claude/skills/<name>/SKILL.md` in the worktree (pruning stale ones on a changed list; see `docs/15-catalog.md`). The agent env carries `JALEBI_GITHUB_TOKEN` = the **selected account's** PAT (all task types — the agent acts as the exact account the owner picked) for GitHub API use, plus the task's **selected env vars** (`envvars.values_for_names` from `tasks.env_vars_json`, merged on top of the pinned env), but **no git push credentials** (`auth_env` is never applied; Jalebi is the only pusher). The env-var values are added to the masker so they're redacted if echoed. Inherited `GH_TOKEN`/`GITHUB_TOKEN`/`JALEBI_GITHUB_TOKEN` are stripped. The child `pid` is recorded on the run row.
5. Streams `handle.events()`; `step`/`message`/`tool_call`/`done`/`error` events are **masked at ingest** (PRD F17: **all PATs** + `secret_patterns`), broadcast live over the per-task SSE channel, and stored in `runs.steps_json` (bounded: 500 steps, 2000-char texts). **`tool_call` is persisted too**, so a reload doesn't lose console lines. A `cancelled` kill's "exited with code -15" error is replaced with a clean "Run cancelled by user." message.
6. Terminal status from event stream **or** watchdog/cancel reason.
7. If `done` and **`publish_mode` says publish** (per-task `"auto"`/`"manual"`; `None` → the global `auto_publish` setting; `issue_fix` defaults to `"auto"`, freeform/manual types to `"manual"`): **only if the branch is ahead of `origin/<target>`** (`commits_ahead > 0` — nothing to PR otherwise; this **no-op gate** also covers a `done` agent that made no commits, and manual publish refuses the same way) → `_publish(mode="new_pr")` (auto-publish always uses `new_pr`): recreate the worktree if it was cleaned, then **sync-before-push** — `merge_origin_into` fetches origin and merges `origin/<target>` into the task branch so the PR is up to date with target's progress and mergable. **On conflict** the merge is **aborted**, the conflicting files are surfaced ("PR would conflict with `<target>`: file1…"), the task goes to `needs_approval`, and nothing is pushed or opened — the user resolves via a follow-up asking the agent to merge `origin/<target>` and resolve, then publishes again. On a clean merge → `push_branch`, then **dedup** — if an **open** PR with `head=jalebi/<taskId>` already exists (same-repo, via `find_pr_by_head`) it is **reused** (never a second PR, even if an agent opened one), else `create_pr`. Closed/merged PRs reusing the branch name are **never** reused (a stale PR from a previous session/wipe must not swallow the publish — verified via `get_pr` defensively, and the same guard clears a stored-but-closed `task.pr_number`). Title/body come from the agent-written `.jalebi/pr.md` when present (`# <title>` + description) — **fallback** to the prompt. Body always gets `Closes #N` derived from **`tasks.issues_json`** (the linked issues, not a `#N` regex over the prompt — so a stray `#3` in the wording never produces a bogus `Closes`) + a Jalebi-task footer + `Co-authored-by`. For `issue_fix`, Jalebi **comments on each linked issue** linking the PR — **only when the PR is newly created**, never on a reuse/follow-up push. On publish failure → task `needs_approval` (manual publish available).

   **Manual publish** dispatches to one of three modes (`apps/server/src/jalebi/queue.py:TaskQueue.publish_task`):
   - `new_pr` (default) — same flow as auto-publish.
   - `update_pr` — fast-forward (or merge) `jalebi/<id>` into the existing PR's head branch (`GitWorkspace.fast_forward_into`) and force-push with `--force-with-lease` (`GitWorkspace.push_existing_branch`). On conflict → `PublishConflict` (409). On lease failure → `PushLeaseFailed` (412, owner must re-fetch). No new PR opened; `Closes #N` not added; no issue comment.
   - `push_branch` — fast-forward (or merge) `jalebi/<id>` into the named branch and force-push. Same conflict / lease-failure semantics. No PR interaction.

   All three modes run in the queue/server process; `auth_env` (git push credentials) is never applied to the agent env, so the agent still cannot push directly. The agent only ever writes to its local `jalebi/<id>` branch — `docs/10-security.md` §"Agent env hardening" + `docs/04-git-workspace.md` §8 cover the lock.
8. Exceptions during the run: the child is killed, `_running` is cleared, the session is rolled back, and the task **and** run are marked `failed` (never left stuck `running`).
9. **Uncommitted-work visibility:** a `done` run with a dirty working tree (`git status --porcelain`) gets a timeline step listing the uncommitted files; if the agent made **no commits at all** but left work behind, the **working-tree diff is captured** into `runs.diff_text` so the work is still visible in the diff viewer. (Committed diffs always take precedence.)

### 4a. Task types

- **`freeform`** — the user's text, plus the shared best-practices/hard-rules `AGENTS.md`. Runs as the **selected account** (its PAT is in the agent env for GitHub API use; no git push creds). `publish_mode` defaults to **`manual`**. **Linked PR (optional):** if the creator links a PR, creation fetches the PR + its current **review comments** into `context_json` (masked) and `AGENTS.md` embeds a `## Linked pull request` block (PR, body, review comments as UNTRUSTED DATA) — so "fix the issues in this PR" tasks see exactly what the reviewers said. Tasks created before this existed (or whose context fetch failed) still get the linked PR number + a fetch-it-yourself note in `AGENTS.md`.
- **`issue_fix`** — task creation fetches the issue (`GET /api/github/context` picker → `github.get_issue`), stores its body in `tasks.context_json` (masked) and the number in `tasks.issues_json`. `AGENTS.md` embeds the issue; the agent implements on the current branch — the worktree is based on the **target branch (single-target model)**, validates, commits, and writes `.jalebi/pr.md` (Jalebi pushes). Jalebi opens the PR into the same target branch (`Closes #N`) and comments on the issue (new-PR only). `publish_mode` defaults to **`auto`**. Requires `issue_number` at creation.
- **`pr_review`** — `TaskQueue._run_review`: the agent runs in a **detached review worktree** checked out at the PR head (`refs/pull/<n>/head`, works for fork PRs too), told to review only (never push). On success Jalebi reads `.jalebi/review.md` (fallback: last message) and posts it as a GitHub PR review **COMMENT** (`POST .../pulls/N/reviews`, event `COMMENT` — never approve/merge). Review tasks never publish. Requires `pr_number` at creation. **Reviewer assignments (Phase 1, PRD F7):** a reviewer task created via `reviews.assign_reviewers` (or `reviewers: [...]` on the create payload) carries a `review_assignments` row; `_run_review` marks it `running` (with the run) at start, `posted` on successful review posting, and `failed` on a run error. Each reviewer = one task, so multiple reviewers run in parallel under concurrency.
- **`screen_finding`** / **`triggered`** — reserved (Phase 2/1).

## 4b. Follow-ups (`TaskQueue._run_followup`, PRD F11)

- `POST /api/tasks/:id/followup` (JSON `{"prompt": ..., "pat_name"?, "model"?}`) requires the task to be **terminal** and a resumable session — the latest run **with a `session_id`** (`tasks.latest_resumable_run`, so a cancelled follow-up run that captured no session falls back to the last good one). The body is masked at ingest; a `followups` row (with the PAT/model override) is recorded against the resumed run.
- The worker resumes with `adapter.resume(cwd=worktree, session_id, prompt + context, env)` — creating a fresh `runs` row (`seq+1`) and streaming live via SSE (watchdog/cancel apply, same as a normal run). PAT/model overrides are honored (defaults: the task's PAT/model).
- **The follow-up runs in the session's own worktree, not the task worktree:** a pr_review session was created in the **review** worktree (`ws/task-<id>-review`, detached at the PR head), so pr_review follow-ups resume there (`create_review_worktree`). Resuming a review session from the task worktree makes opencode's headless `--session` resume return an empty model stream and hang forever (see `docs/03-adapters.md` §6) — the run would sit `running` with an empty timeline until the stall guard or timeout fires.
- On `done` + `auto_publish`: if the branch is ahead it publishes — reusing the task's existing `pr_number` **or** deduping by `head` if a PR already exists, else opening a new PR.
- **pr_review follow-ups post the review too:** both the initial review and a follow-up resume run through the shared `_post_review` — a `done` run posts the review worktree's `.jalebi/review.md` (fallback: the last assistant message) to the PR and sets the assignment to `posted`. A `done` run **with no review content is a failure**, not a silent success: the run/task flip to `failed` with a "nothing to post" diagnostic so a run whose agent ended without a deliverable is never shown as delivered. Follow-up artifacts are captured from the **review** worktree (not the task worktree), so a freshly written `review.md` is not missed.
- Cancelling a resumed run kills the child; because a cancelled run may capture no `session_id`, follow-ups fall back to the latest run that has one.

## 4c. Commit statuses (PRD F15, Phase 2)

- **Gate:** per-repo opt-in (`repos.check_runs_enabled`) AND task type (`issue_fix`/`pr_review`). Both must hold.
- **Why statuses, not check runs:** GitHub's check-runs API is GitHub-App only; PATs cannot write it. Commit statuses (`POST /repos/{o}/{r}/statuses/{sha}`) are PAT-writable and branch-protection-requireable — the same merge gate.
- **Run start:** `_start_status` posts `pending` — `pr_review` on the PR head SHA; `issue_fix` only if the `jalebi/<id>` branch already exists on the remote (re-run/follow-up), else deferred to publish.
- **Run terminal:** `_complete_status` posts the final state from `task.status` (`done→success`, `failed`/`timed_out→failure`, `cancelled`/`interrupted→error`, else `pending`).
- **Exception paths:** a worker crash (`_run_task`/`_run_review`/`_run_followup` `except`) now also calls `_complete_status` (best-effort, guarded) after the run is marked `failed`, so a crashed run never leaves a forever-blocking `pending` on the head SHA.
- **Publish:** `_publish_status` posts the final state on the just-pushed head (auto-publish in `_stream_and_finish`; manual publish in `publish_task`).
- **Non-fatal:** all status API calls are best-effort; a failure is logged and never fails the task.
- Registry rows live in `check_runs` (see `docs/02`); `tasks.check_run_id` tracks the latest.

## 5. Timeouts (PRD F16)

- Per-task `timeout_minutes`, defaulting to `settings.default_timeout_minutes` (**default 60 minutes**; PRD §F16 said 30 — owner-approved deviation). A daemon **watchdog thread** enforces it: on expiry it kills the child (SIGTERM → 5s grace → SIGKILL) and the run resolves to `timed_out`. A timeout of `0` fires immediately (used in tests).
- **Stall guard:** a second daemon **stall watchdog** kills the child if the agent process stays alive but emits **no event for `settings.stall_timeout_seconds`** (default **600s**; checked from run start). The run then resolves to `failed` with a diagnostic step ("Agent produced no output for Ns — …"). A quiet-but-healthy tool phase (e.g. a sub-agent that stops reporting to the parent stream) therefore has a generous window, and if it still trips, **auto-recovery** (see §7) restarts it instead of stranding the task. The total-budget timeout remains the last line of defence.

## 5a. Notifications (ntfy)

- **Sender** (`src/jalebi/notify.py`): best-effort httpx POST; never raises into the queue. The **merged `ntfy_topic` setting** is either a bare topic (→ `https://ntfy.sh/<topic>`) or a full URL to a self-hosted server (`ntfy_url` was folded into it). Messages are masked (PATs + secret patterns) before send.
- **JSON publishing (docs.ntfy.sh/publish/):** the JSON body is POSTed to the **server root** with `topic` inside the body — *not* to `/topic` (which would render the raw JSON as the message). This enables **Markdown** (`markdown: true`), tags, priorities, a **click action** and a **"Open task" action button** that deep-links to the Jalebi task page (`http://127.0.0.1:3456/tasks/<id>`).
- **Terminal notifications:** when a run ends (`done` / `failed` / `timed_out` / `cancelled` / `needs_approval`), a markdown summary (type, repo, status, PR link, last agent message) is pushed, gated by `notify_on_done` / `notify_on_failed` / `notify_on_needs_approval`.
- **Progress notifications:** a third watchdog thread (`_progress_notify_loop`) pushes "still running — Nm elapsed — last: <agent message>" every `notify_progress_interval_minutes` (default 30), starting at the first interval mark, while the agent process is alive (gated by `notify_on_progress`; reads settings live).
- **Test endpoint:** `POST /api/notify/test` sends a sample markdown push with a click action and reports ok/error (Settings → Notifications → "Send test notification").

## 6. Cancellation (PRD F3)

- `cancel()` sets a reason and kills the child process group (SIGTERM → SIGKILL); the event stream ends and the run resolves to `cancelled`. Cancellation works for a task that is `queued` (status flip + `queue.cancel` so an in-flight pickup is flagged) or `running` (child killed). Agent children spawn in their own session (`start_new_session`), so killing the process group reaches MCP servers/grandchildren instead of orphaning them.
- On run completion the worktree's committed changes are snapshotted into `runs.diff_text` (masked **before** truncation, byte-capped at 512 KB) for the PRD §12 diff viewer — only for non-review tasks (the review worktree is the PR itself). Best-effort; a capture failure never fails the run.

## 7. Retries & auto-recovery (PRD F16)

- `rerun` reuses the same task row + worktree (`create_worktree` resumes an existing worktree); a fresh agent session runs (new `runs` row, new `seq`). `retry_count` counts **auto-recoveries only** — a manual rerun never touches it.
- **Auto-recovery (`retry_policy`, ships ON):** a run that ends `failed` (incl. **stalled**) or `timed_out` is recovered automatically, for **every task type** (each task has an expected deliverable). Unbounded by design — every run is still bounded by its own (escalating) timeout and terminal/progress notifications keep the owner informed:
  - **timeout / other failure** → resumes the last session with `retry_policy.continue_prompt` (default `"continue"`);
  - **stall** (process hung, no output) → **fresh re-run** instead of resuming a session that may be wedged and would just hang again;
  - no resumable session → fresh re-run.
  - Each attempt **derives** an escalated timeout from `task.retry_count` (`base × timeout_multiplier^attempts`, capped at `retry_policy.max_timeout_minutes`) — `task.timeout_minutes` is never permanently mutated, and a `done` run **resets `retry_count`**, so a later manual rerun starts from the base timeout again.
  - Recovery resumes are tagged **auto** and are **not** recorded as user follow-ups (they aren't; the recovery step is on the failed run's timeline).
  - Applied in **all three run paths**: `_run_task` (normal + `pr_review`) and `_run_followup` (previously unretried). `task.retry_count` increments for observability; a timeline step notes "Auto-recovering — re-running with a longer timeout (Nm, attempt N)."
- **Review worktrees re-sync on every run:** `create_review_worktree` re-fetches `refs/pull/<n>/head` and hard-resets the detached worktree to the **current** PR head (safe — review worktrees never hold agent-pushed work), so a follow-up/re-run review sees the latest code instead of the originally-checked-out head.

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
