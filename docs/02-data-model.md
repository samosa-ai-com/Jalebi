# 02 — Data Model

> **Scope:** Full SQLite schema + relationships. Update this file when the schema or migrations change.

---

## 1. Storage layout

- **SQLite** via better-sqlite3 + Drizzle ORM.
- Data dir (default `~/.jalebi/`):
  - `data.db` — the SQLite database.
  - `secrets.json` — PAT + secrets, `0600` permissions.
  - `repos/` — bare mirrors.
  - `ws/` — worktrees.
  - `agents/` — catalog agent files (personality + skills).
  - `logs/` — run logs.

## 2. Tables

### `repos`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `full_name` | text | `owner/repo` |
| `default_branch` | text | |
| `clone_url` | text | |
| `pat_scope` | text | granted scopes snapshot |
| `webhook_registered` | bool | |
| `poll_fallback` | bool | |
| `check_runs_enabled` | bool | |
| `last_checked_at` | datetime | |

### `tasks`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `type` | text | `issue_fix` \| `pr_review` \| `freeform` \| `screen_finding` \| `triggered` |
| `repo_id` | int FK → repos | |
| `source_branch` | text | base to branch off |
| `target_branch` | text | PR base |
| `agent_id` | int FK → catalog_agents | nullable (default build agent) |
| `model` | text | nullable |
| `cli` | text | backend override |
| `prompt` | text | instructions |
| `status` | text | `queued` \| `running` \| `waiting_review` \| `needs_approval` \| `done` \| `failed` \| `timed_out` \| `interrupted` |
| `timeout_minutes` | int | default 30 |
| `retry_count` | int | |
| `pr_number` | int | nullable |
| `check_run_id` | int | nullable |
| `created_at` | datetime | |
| `updated_at` | datetime | |

### `runs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | |
| `seq` | int | run sequence within task |
| `session_id` | text | CLI session id (for resume) |
| `cli` | text | |
| `model` | text | |
| `started_at` | datetime | |
| `finished_at` | datetime | |
| `status` | text | |
| `steps_json` | text | timeline steps |
| `artifacts_json` | text | artifact refs |

### `followups`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | |
| `run_id` | int FK → runs | |
| `body` | text | |
| `created_at` | datetime | |

### `catalog_agents`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `name` | text | |
| `kind` | text | `general` \| `reviewer` |
| `cli` | text | optional backend override |
| `model` | text | optional pin |
| `personality_md` | text | markdown → `AGENTS.md` |
| `skills_json` | text | skill file refs |
| `custom_instructions` | text | appended to task prompt |
| `enabled` | bool | |
| `created_at` | datetime | |

### `review_assignments`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | |
| `agent_id` | int FK → catalog_agents | |
| `run_id` | int FK → runs | |
| `pr_number` | int | |
| `status` | text | `queued` \| `running` \| `posted` \| `failed` |

### `trigger_rules`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `repo_id` | int FK → repos | |
| `event` | text | e.g. `pull_request.opened` |
| `action` | text | `start_review` \| `triage_issue` \| `create_task` \| `rerun_review` |
| `branch_filter` | text | optional |
| `label_filter` | text | optional |
| `author_filter` | text | optional |
| `agent_ids_json` | text | target agents/reviewers |
| `custom_instructions` | text | |
| `enabled` | bool | |

### `event_deliveries`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `github_delivery_id` | text UNIQUE | idempotency key |
| `event` | text | |
| `repo_id` | int FK → repos | |
| `payload_json` | text | |
| `received_at` | datetime | |
| `matched_rule_id` | int | nullable |
| `status` | text | |
| `result` | text | |

### `check_runs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `task_id` | int FK → tasks | |
| `run_id` | int FK → runs | |
| `repo_id` | int FK → repos | |
| `head_sha` | text | |
| `name` | text | e.g. `Jalebi / review (security-auditor)` |
| `status` | text | `queued` \| `in_progress` \| `completed` |
| `conclusion` | text | `success` \| `failure` \| `neutral` \| `cancelled` |

### `screenings`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `repo_id` | int FK → repos | |
| `name` | text | |
| `system_prompt` | text | |
| `cadence_cron` | text | |
| `scope_branch` | text | |
| `enabled` | bool | |
| `notify_ntfy` | bool | |

### `screening_runs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `screening_id` | int FK → screenings | |
| `head_sha` | text | baseline dedup |
| `status` | text | |
| `started_at` | datetime | |
| `finished_at` | datetime | |
| `findings_json` | text | |

### `artifacts`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `run_id` | int FK → runs | |
| `path` | text | |
| `size` | int | |
| `created_at` | datetime | |

### `settings`

| Column | Type | Notes |
|--------|------|-------|
| `key` | text PK | |
| `value` | text | |

Settings keys: `concurrency`, `auto_publish`, `ntfy_topic`, `default_timeout_minutes`, `retry_policy`, `secret_patterns_json`, `artifact_ttl_days`, etc.

## 3. Relationships

```
repos 1───* tasks
tasks 1───* runs
tasks 1───* followups
runs 1───* followups
tasks *───1 catalog_agents   (agent_id)
tasks 1───* review_assignments
catalog_agents 1───* review_assignments
repos 1───* trigger_rules
repos 1───* event_deliveries
tasks 1───* check_runs
runs 1───* check_runs
repos 1───* screenings
screenings 1───* screening_runs
runs 1───* artifacts
```

## 4. Key invariants

- `event_deliveries.github_delivery_id` is **UNIQUE** — this is the idempotency guarantee for webhook re-delivery (PRD §F14).
- `runs.session_id` is persisted so follow-ups survive restarts (PRD §F11).
- `screening_runs.head_sha` enables baseline dedup — skip a screen if HEAD is unchanged (PRD §F10).
- `check_runs` are matched by name + head SHA so follow-ups update the existing check rather than duplicating (PRD §F15).