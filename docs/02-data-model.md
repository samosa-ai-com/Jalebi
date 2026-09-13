# 02 — Data Model

> **Scope:** SQLite schema + relationships as implemented. Update this file when the schema or migrations change.

---

## 1. Storage layout

- **SQLite** via SQLAlchemy 2.0 (`jalebi/db.py`), migrations via **Alembic** (`jalebi/migrations/`).
- Data dir (default `~/.jalebi/`, override `JALEBI_DATA_DIR`):
  - `data.db` — the SQLite database.
  - `secrets.json` — PAT + secrets, `0600` permissions (added with the GitHub client).
  - `repos/` — bare mirrors.
  - `ws/` — worktrees.
  - `agents/` — catalog agent files (personality + skills).
  - `logs/` — run logs.
- **Schema evolution:** one Alembic migration per change. Tables are added incrementally by phase — Phase 0 ships only the tables below; catalog/triggers/screening/check-run tables arrive with their phases (see PRD §10).

## 2. Phase-0 tables

### `repos`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `full_name` | text UNIQUE | `owner/repo` |
| `default_branch` | text, default `'main'` | |
| `clone_url` | text | never exposed via the API |
| `pat_scope` | text null | granted scopes snapshot |
| `pat_name` | text null | the account that owns this repo (required at connect; no default) |
| `connected` | bool, default 1 | soft-disconnect flag |
| `webhook_registered` | bool, default 0 | |
| `poll_fallback` | bool, default 0 | |
| `check_runs_enabled` | bool, default 0 | |
| `last_checked_at` | datetime null | |

### `tasks`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `type` | text CHECK | `issue_fix` \| `pr_review` \| `freeform` \| `screen_finding` \| `triggered` |
| `repo_id` | int FK → repos | |
| `source_branch` | text, default `'main'` | worktree base for freeform (and other non-issue_fix types); **unused for `issue_fix`** (single-target model). May be the sentinel **`pr/<N>/head`** (freeform only, must match the linked `pr_number`) meaning "base the worktree on the current head of PR #N" — used for fork PRs whose branch never exists on origin; diff/conflict checks for these tasks run against `target_branch` via `tasks.effective_diff_base()` |
| `target_branch` | text, default `'main'` | PR base; also the worktree base for `issue_fix` |
| `agent_id` | text null | catalog agent slug — **no FK yet**; FK added in Phase 1 |
| `model` | text null | |
| `cli` | text null | backend override |
| `pat_name` | text null | the **account** that runs this task (required at creation; no default) |
| `issues_json` | text null | JSON list of linked issue numbers |
| `prs_json` | text null | JSON list of linked PR numbers (any type that links a PR, e.g. `pr_review` and freeform "fix the issues in this PR") |
| `context_json` | text null | masked issue/PR context embedded into the agent brief; fetched at creation for **any** linked issue/PR (not just `issue_fix`/`pr_review`). PR context includes `reviews` — the PR's current review comments (masked), so a freeform task linked to a PR can address them directly. Review text is truncated (per-comment and total) with a marker so large reviews can't bloat the agent brief. PR entries also carry fork metadata (`head_repo`, `head_sha`, `is_fork`, `maintainer_can_modify`) backing the fork-PR fix flow. Also stores `attention_dismissed: true` and `attention_dismissed_at` when the owner dismisses attention. A dismissal is scoped to its run: `queue._prepare_run` clears it (via `tasks.clear_attention_dismissal`) whenever a new run starts, so a rerun/follow-up re-arms attention instead of hiding the new run's future `needs_you`. |
| `env_vars_json` | text null | JSON list of env-var **names** injected into the agent subprocess env |
| `prompt` | text | instructions |
| `status` | text CHECK, default `'queued'` | `queued` \| `running` \| `waiting_review` \| `needs_approval` \| `done` \| `failed` \| `timed_out` \| `interrupted` \| `cancelled` |
| `timeout_minutes` | int, default 60 | |
| `retry_count` | int, default 0 | |
| `pr_number` | int null | |
| `publish_mode` | text null | `'auto'` \| `'manual'` \| NULL (fall back to the global `auto_publish` setting). `issue_fix` defaults to `auto`; freeform/manual types to `manual`. |
| `address_reviews` | bool, default false | creation-time "address the review comments on the linked PR" (freeform only; 400 otherwise or without `pr_number`). Guarantees the address-reviews instruction in the run prompt even when no reviews were fetched at creation (agent self-fetches). |
| `check_run_id` | int null | **no FK yet**; check_runs table arrives in Phase 2 |
| `created_at` | datetime | naive local (app timezone, see `jalebi/clock.py`) |
| `updated_at` | datetime | naive local (app timezone, see `jalebi/clock.py`) |

Indexes: `repo_id`, `status`.

### `runs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | |
| `seq` | int, default 1 | run sequence within task |
| `session_id` | text null | CLI session id (for resume) |
| `cli` | text null | |
| `model` | text null | |
| `pat_name` | text null | account the run used |
| `pid` | int null | agent child pid (for crash recovery) |
| `started_at` | datetime null | |
| `finished_at` | datetime null | |
| `status` | text null | |
| `steps_json` | text null | timeline steps (cache) |
| `artifacts_json` | text null | artifact refs (cache; relational `artifacts` is the primary record) |
| `diff_text` | text null | run-end diff snapshot (masked, ≤512 KB; PRD §12) |

Index: `task_id`.

### `followups`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | |
| `run_id` | int null FK → runs | |
| `body` | text | |
| `pat_name` | text null | account override for the resume |
| `model` | text null | model override for the resume |
| `cli` | text null | backend override for the resume (NULL = task backend reused) |
| `created_at` | datetime | application timestamp (`screening.py` is the sole writer) |

Index: `task_id`.

### `artifacts`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `run_id` | int FK → runs | |
| `path` | text | primary record |
| `size` | int, default 0 | |
| `created_at` | datetime | |

Index: `run_id`.

**Store:** artifact files live at `<data-dir>/artifacts/<run_id>/<relative path>` (copied from the worktree at run completion). `runs.artifacts_json` caches `[{path, size}]` refs. Pruned per `artifact_ttl_days` at startup.

### `env_vars`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `name` | text | env var name |
| `value` | text | **secret** — never returned in full by the API; masked if echoed by an agent |
| `repo_id` | int FK → repos, null | NULL = global; non-NULL = scoped to one repo |
| `created_at` | datetime | |
| `updated_at` | datetime | |

Unique: `(name, repo_id)`. Index: `repo_id`. See `docs/14-env-vars.md`.

### `review_assignments` (Phase 1 — PRD F7)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | the reviewer's own `pr_review` task |
| `agent_id` | text | catalog agent slug (kind `reviewer`) |
| `run_id` | int FK → runs, null | the reviewer run (set when it starts) |
| `pr_number` | int | the PR under review |
| `repo_id` | int FK → repos | |
| `status` | text | `queued` \| `running` \| `posted` \| `failed` |
| `created_at` | datetime | |

Indexes: `task_id`, `pr_number`. Each reviewer runs as its own `pr_review`
task; the assignment is a lightweight registry (task ↔ agent ↔ PR ↔ repo) so the
PR card and the webhook flow can show posted status. See `docs/05` §6.

### `trigger_rules` (Phase 1 — PRD F14)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `repo_id` | int FK → repos | |
| `event` | text | e.g. `pull_request.opened`, `issues.opened`, `push` |
| `action` | text | `start_review` \| `triage_issue` \| `create_task` \| `rerun_review` |
| `branch_filter` | text, null | match head OR base ref |
| `label_filter` | text, null | JSON list — all must be present |
| `author_filter` | text, null | match PR/issue author login |
| `agent_ids_json` | text, null | JSON list of catalog agent ids |
| `custom_instructions` | text, null | task prompt for triage/create_task |
| `enabled` | bool | disabled rules never fire |
| `created_at` | datetime | |

Index: `repo_id`. See `docs/16-triggers.md`.

### `event_deliveries` (Phase 1 — PRD F14)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `github_delivery_id` | text UNIQUE | `X-GitHub-Delivery` — idempotency |
| `event` | text | `X-GitHub-Event` |
| `action` | text, null | payload `action` |
| `repo_id` | int FK → repos, null | |
| `repo_full_name` | text, null | |
| `payload_json` | text | raw body (for replay) |
| `received_at` | datetime | |
| `status` | text | `received` \| `matched` \| `ignored` \| `failed` |
| `result` | text, null | JSON `{"rules": [{"rule_id", "action", "work"}, ...]}` — full set of matched rules (the source of truth) |

Index: `repo_id`. The UNIQUE `github_delivery_id` makes re-deliveries no-ops;
the stored payload enables replay. When multiple rules match a single
delivery, every matched rule's id is recorded in `result.rules[].rule_id`
(Step 45/M5 dropped the single-value `matched_rule_id` column, which
could only record `rules[0].id`).

### `screenings` (Phase 2 — PRD F10)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `repo_id` | int FK → repos (CASCADE) | the repo being audited |
| `name` | text | human-readable screen name |
| `system_prompt` | text | the audit system prompt |
| `cadence_cron` | text, default `'0 6 * * *'` | 5-field cron (see `jalebi/cron.py`) — matched against the app's **configured timezone** (default: the machine's local zone) |
| `scope_branch` | text, null | NULL = repo default branch |
| `cli` | text, null | optional backend pin (default `opencode`); NULL = default adapter |
| `model` | text, null | optional model pin; NULL = adapter default |
| `enabled` | bool | disabled screens never run |
| `notify_ntfy` | bool | push findings via ntfy |
| `created_at` / `updated_at` | datetime | |

Index: `repo_id`. Screening is **notify-only** — it never creates tasks/PRs;
the owner converts findings into `screen_finding` tasks. See `docs/07`.

### `screening_runs` (Phase 2 — PRD F10)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `screening_id` | int FK → screenings (CASCADE) | |
| `head_sha` | text, null | the audited HEAD — also the baseline-dedup watermark |
| `status` | text, default `'queued'` | `queued` \| `running` \| `done` \| `failed` |
| `started_at` / `finished_at` | datetime, null | |
| `findings_json` | text, null | parsed JSON array of findings |
| `output_json` | text, null | masked raw output `{"message": …}` |
| `error` | text, null | run failure detail |

Index: `screening_id`. A screen skips a tick when its last terminal run
(`done`/`failed`) audited the same `head_sha` (baseline dedup).

### `screening_dealt` (Phase 4 — rerun context)

Owner-handled findings. One row = dealt (task created, accepted risk, or
manually dismissed). The key is the canonical `fingerprint`
`[screening_id, title, file, line]` (compact JSON, `file` NULL → `""`),
stored — never recomputed from nullable columns (SQLite treats NULLs as
distinct in UNIQUE constraints).

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `screening_id` | int FK → screenings (CASCADE) | |
| `fingerprint` | text, NOT NULL | canonical key; `UNIQUE(screening_id, fingerprint)` |
| `title` / `file` / `line` | text / text / int, null | auxiliary, inspection only |
| `created_at` | datetime | |

Index: `screening_id`. Powers two behaviors: the audit prompt's known-open
context (`get_open_findings`) and dealt filtering in ntfy notifications.
Migration: `e7f8a9b0c1d2`; the current schema head is `b4c5d6e7f8a9`. See `docs/07`.

### `check_runs` (Phase 2 — PRD F15)

Jalebi's **registry of the commit statuses it set** (commit statuses, not GitHub check runs — the check-runs API is GitHub-App only). One row per `(task_id, head_sha, context)`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | the task reporting this status |
| `run_id` | int FK → runs, null | the run that set it |
| `repo_id` | int FK → repos | |
| `head_sha` | text, null | the SHA the status is attached to |
| `name` | text | context: `Jalebi / fix` \| `Jalebi / review` |
| `status` | text, default `'queued'` | `in_progress` (pending) \| `completed` |
| `conclusion` | text, null | `NULL` while pending; terminal state `success` \| `failure` \| `error` |
| `github_check_id` | int, null | GitHub-side commit-status id |
| `created_at` | datetime | |

Indexes: `task_id`, `head_sha`. `tasks.check_run_id` points at the latest row
and is deliberately **FK-less** (a SQLite batch rebuild of the FK parent
`tasks` is the Step-37 migration hazard). A follow-up updates the existing row
(matched by `(task_id, head_sha, name)`); a new pushed head gets a fresh row.
See `docs/05` §5.

### `settings`

| Column | Type | Notes |
|--------|------|-------|
| `key` | text PK | |
| `value` | text | JSON-encoded |

Settings keys (defaults in `jalebi/settings.py`): `concurrency` (4), `auto_publish` (true), `review_nitpick_mode` (true), `auto_nudge` (false), `ntfy_topic` ("" — merged: bare topic **or** full URL), `default_timeout_minutes` (60), `retry_policy` (`{"auto_retry": true, "continue_prompt": "continue", "timeout_multiplier": 2, "max_timeout_minutes": 180, "max_attempts": 3, "non_retryable_patterns": [...]}` — recovery is **bounded** by `max_attempts`; failures matching a pattern fail immediately), `secret_patterns` (`[]`), `artifact_ttl_days` (7), `default_backend` (`"opencode"`), `default_model` (`""`, required), `adapter_model_lists` (`{}` — per-backend model-dropdown overrides), `notify_on_done` (true), `notify_on_failed` (true), `notify_on_progress` (true), `notify_on_needs_approval` (true), `notify_progress_interval_minutes` (30). **Every key is materialized as a row at startup (`seed_defaults`)** — settings are persistent and never held in memory; stored values override the code default. `enabled_backends` (default all three) is the backend allow-list consumed via `GET /api/backends`; `GET /api/timezones` serves the IANA list for the Timezone dropdown.

### `catalog_agents` (Phase 1 — PRD F6)

| Column | Type | Notes |
|--------|------|-------|
| `id` | text PK | slug, e.g. `security-auditor` |
| `name` | text | display name |
| `kind` | text | `general` \| `reviewer` |
| `cli` | text, null | backend override (opencode/codex/claude) |
| `model` | text, null | pinned model |
| `personality_md` | text | markdown merged into the worktree `AGENTS.md` |
| `skills_json` | text, null | legacy inline `[{name, content}]` extras (appended after library skills) |
| `skill_ids_json` | text, null | ordered JSON list of linked `catalog_skills` slugs |
| `description` | text | one-liner shown on cards and in pickers |
| `avatar` | text, null | avatar id (`apps/web/public/avatars/*.svg`); NULL = auto-assign |
| `custom_instructions` | text | appended to the task prompt |
| `enabled` | bool | disabled agents aren't selectable on new tasks |
| `created_at` | datetime | |

`tasks.agent_id` references `catalog_agents.id` by slug but is **FK-less by
design** (a SQLite batch rebuild of the FK-referenced `tasks` parent is the
Step-37 migration hazard); validity is enforced in the service layer and at run
time. Skill content lives in the DB and is materialized directly into the task
worktree (`.claude/skills/<name>/SKILL.md`) at run time (see
`docs/15-catalog.md`).

### `catalog_skills` (skills library)

| Column | Type | Notes |
|--------|------|-------|
| `id` | text PK | slug, e.g. `secure-coding` |
| `name` | text | display name |
| `description` | text | one-liner for pickers/cards |
| `content` | text | markdown body (≤100 KB) |
| `tags_json` | text, null | JSON list of tag strings (≤12) |
| `created_at` / `updated_at` | datetime | |

Agents link library skills via `catalog_agents.skill_ids_json` (ordered JSON
list, FK-less by design — unknown slugs refused at write time, skipped at run
time). Deleting a linked skill is refused (409 + linking agents). Seed content
is versioned (`catalog_seed_version` setting) — see `docs/15-catalog.md` §8.

### `notifications` (task-notification backend)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | `ON DELETE CASCADE`, not null |
| `run_id` | int null FK → runs | `ON DELETE CASCADE`, nullable |
| `kind` | text | `task_done` \| `task_failed` \| `needs_input` |
| `title` | text | notification headline |
| `body` | text null | short summary or agent message snippet |
| `read_at` | datetime null | null means unread |
| `created_at` | datetime | default now, server_default `CURRENT_TIMESTAMP` |

Indexes: `(task_id, created_at)`, `(created_at)`, and the partial unique
`(task_id, kind) WHERE read_at IS NULL`. The last is the database-enforced
unread-dedup invariant: reading a notification permits a later event of the
same kind to create a new row.

Indexes: `(task_id, created_at)`, `(created_at)`.

## 3. Relationships (Phase 0 + Phase 1 catalog + reviewers)

```
repos 1───* tasks
tasks 1───* runs
tasks 1───* followups
runs  1───* followups  (run_id nullable)
runs  1───* artifacts
tasks 1───* notifications
runs  0───* notifications  (run_id nullable)
repos 0───* env_vars   (repo_id nullable = global)
tasks 0───1 catalog_agents  (agent_id slug, FK-less by design)
catalog_agents *───* catalog_skills  (skill_ids_json ordered slug list, FK-less)
repos 1───* review_assignments
tasks 1───* review_assignments  (task_id = the reviewer's own pr_review task)
runs  0───1 review_assignments  (run_id, set when the reviewer run starts)
```

## 4. Key invariants

- `repos.full_name` is **UNIQUE** (upsert-safe repo tracking).
- `tasks.type` / `tasks.status` are CHECK-constrained to the PRD enums; `status` includes `cancelled` (abort/cancel is a terminal task state, PRD §F3).
- `tasks.agent_id` and `tasks.check_run_id` are plain nullable columns until their target tables exist (Phase 1 / Phase 2 respectively).
- `runs.session_id` is persisted so follow-ups survive restarts (PRD §F11).
- Static column defaults are declared at the **DB level** (`server_default`) as well as the model level, so raw SQL inserts behave like ORM inserts.
- `alembic_version` tracks the applied revision; `jalebi.db.run_migrations()` upgrades to `head` on app startup and via the `alembic` CLI.

## 5. Not yet implemented (later phases)

None — all Phase-0/1/2 tables are materialized. (Phase 3 adds no new tables.)

---

## 6. Phase 4 schema additions

- **`runs.git_sha_start`** (`runs.git_sha_start`, `String(64)`, nullable) — added by Alembic migration `7b4c5d6e7f80`. The full SHA of the worktree's HEAD captured pre-spawn in each of `_run_task`, `_run_review`, and `_run_followup`; `None` when the worktree is unborn. Suffix `-dirty` when `git status --porcelain` is non-empty at capture time.
- **`runs.git_sha_end`** (`runs.git_sha_end`, `String(64)`, nullable) — same migration, captured in `_stream_and_finish` after the agent process exits. Best-effort (a transient git error never blocks run finalization); same `-dirty` suffix semantics.
- Exposed via `tasks.run_to_dict(run)` keys `"git_sha_start"` and `"git_sha_end"` (`None` when unset).

- **T2 adds no new tables.** The polling observer stores normalized `PRFacts` in an in-memory map (`(repo_id, pr_number) → PRFacts`) — ephemeral by design; the poller refetches every 30 s so a durable table would only avoid a bounded cold-start GitHub hammering (PRD Goal #10). The `event_deliveries` table is the durable analog. The pre-existing `repos.last_checked_at` column is updated after every successful tick; the pre-existing `repos.poll_fallback` column (Boolean, default false) is now also settable via `PATCH /api/repos/<id>`.

- **T4 (`8a9b0c1d2e30`) added three new tables:**
  - `task_dependencies(task_id FK→tasks, depends_on_id FK→tasks, created_at)` — composite PK `(task_id, depends_on_id)`, self-ref CHECK, two-side CASCADE. Dep edges are dropped from both directions inside `delete_tasks_cascade`.
  - `task_events(id, task_id FK→tasks, run_id FK→runs, seq, payload_json, created_at)` — durable SSE timeline (Phase 4 T4.3). Per-(task, run) cap at 2000 (`prune_task_events` startup sweep).
  - `nudges(id, task_id FK→tasks, signature, kind, created_at)` — auto-nudge dedup (Phase 4 T4.2). Unique pair `(task_id, signature)`.

- **Delete cascade (`tasks.delete_tasks_cascade`):** TaskDependency → Followup → ReviewAssignment → **CheckRun** → Artifact → Run → Task, plus the task's `task_events` rows (deleted by the caller in prune; the task-delete route relies on FK CASCADE for events). `check_runs.task_id`/`run_id` carry **no** `ON DELETE CASCADE` (the SQLite batch-rebuild hazard), so check runs are deleted explicitly — deleting a task that ever reported a commit status would otherwise `IntegrityError` under `PRAGMA foreign_keys=ON`.
  - `tasks.status` widened to include `"blocked"` (T4.1). A blocked task sits with deps unmet; cleared by `cascade_unblock` when the last unsatisfied dep finishes.

- **Task Notifications (`a2b3c4d5e6f7_notifications.py` + `b4c5d6e7f8a9_notification_dedup.py`):**
  - `notifications(id PK, task_id FK→tasks ON DELETE CASCADE, run_id FK→runs ON DELETE CASCADE, kind, title, body, read_at, created_at)`
  - Indexes: `(task_id, created_at)`, `(created_at)`, and partial-unique `(task_id, kind) WHERE read_at IS NULL` (the migration first retains the newest row of any historic duplicate group).
  - Records persistent notifications on terminal states (`task_done`, `task_failed`) and input requests (`needs_input`). Unread deduplication on `(task_id, kind)` avoids retry spam; bounded retention automatically prunes older rows keeping the newest 500.
