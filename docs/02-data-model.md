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
| `source_branch` | text, default `'main'` | worktree base for freeform (and other non-issue_fix types); **unused for `issue_fix`** (single-target model) |
| `target_branch` | text, default `'main'` | PR base; also the worktree base for `issue_fix` |
| `agent_id` | text null | catalog agent slug — **no FK yet**; FK added in Phase 1 |
| `model` | text null | |
| `cli` | text null | backend override |
| `pat_name` | text null | the **account** that runs this task (required at creation; no default) |
| `issues_json` | text null | JSON list of linked issue numbers |
| `prs_json` | text null | JSON list of PR numbers (review tasks) |
| `context_json` | text null | masked issue/PR context embedded into the agent brief |
| `env_vars_json` | text null | JSON list of env-var **names** injected into the agent subprocess env |
| `prompt` | text | instructions |
| `status` | text CHECK, default `'queued'` | `queued` \| `running` \| `waiting_review` \| `needs_approval` \| `done` \| `failed` \| `timed_out` \| `interrupted` \| `cancelled` |
| `timeout_minutes` | int, default 60 | |
| `retry_count` | int, default 0 | |
| `pr_number` | int null | |
| `publish_mode` | text null | `'auto'` \| `'manual'` \| NULL (fall back to the global `auto_publish` setting). `issue_fix` defaults to `auto`; freeform/manual types to `manual`. |
| `check_run_id` | int null | **no FK yet**; check_runs table arrives in Phase 2 |
| `created_at` | datetime | naive UTC |
| `updated_at` | datetime | naive UTC |

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
| `created_at` | datetime | |

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

### `settings`

| Column | Type | Notes |
|--------|------|-------|
| `key` | text PK | |
| `value` | text | JSON-encoded |

Settings keys (defaults in `jalebi/settings.py`): `concurrency` (4), `auto_publish` (true), `ntfy_topic` ("" — merged: bare topic **or** full URL), `default_timeout_minutes` (60), `retry_policy` (`{"auto_retry": false}`), `secret_patterns` (`[]`), `artifact_ttl_days` (7), `agent_cli` (`"opencode"`), `notify_on_done` (true), `notify_on_failed` (true), `notify_on_progress` (true), `notify_on_needs_approval` (true), `notify_progress_interval_minutes` (30). **Every key is materialized as a row at startup (`seed_defaults`)** — settings are persistent and never held in memory; stored values override the code default.

### `catalog_agents` (Phase 1 — PRD F6)

| Column | Type | Notes |
|--------|------|-------|
| `id` | text PK | slug, e.g. `security-auditor` |
| `name` | text | display name |
| `kind` | text | `general` \| `reviewer` |
| `cli` | text, null | backend override (opencode only today) |
| `model` | text, null | pinned model |
| `personality_md` | text | markdown merged into the worktree `AGENTS.md` |
| `skills_json` | text, null | JSON list of `{name, content}` markdown files |
| `custom_instructions` | text | appended to the task prompt |
| `enabled` | bool | disabled agents aren't selectable on new tasks |
| `created_at` | datetime | |

`tasks.agent_id` references `catalog_agents.id` by slug but is **FK-less by
design** (a SQLite batch rebuild of the FK-referenced `tasks` parent is the
Step-37 migration hazard); validity is enforced in the service layer and at run
time. Skill content lives in the DB and is materialized directly into the task
worktree (`.claude/skills/<name>/SKILL.md`) at run time (see
`docs/15-catalog.md`).

## 3. Relationships (Phase 0 + Phase 1 catalog + reviewers)

```
repos 1───* tasks
tasks 1───* runs
tasks 1───* followups
runs  1───* followups  (run_id nullable)
runs  1───* artifacts
repos 0───* env_vars   (repo_id nullable = global)
tasks 0───1 catalog_agents  (agent_id slug, FK-less by design)
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

`check_runs`, `screenings`, `screening_runs`, `findings` — created by future migrations per PRD §10.
