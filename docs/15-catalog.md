# 15 — Agent Catalog (PRD F6)

> **Scope:** The catalog of named agents (personality + skills + optional CLI/model),
> how they are stored, and how they drive task execution. Update this file for any
> catalog work.

---

## 1. What a catalog agent is

A catalog agent is deliberately **not** a new agent type. It is a lightweight,
user-configured bundle:

- **`id`** — a slug (`security-auditor`) used to select the agent on a task.
- **`name`** — a display label.
- **`kind`** — `general` | `reviewer`. Reviewers get the reviewer workflow
  (see `docs/05` §6 and PRD §F7); `general` agents are plain build agents.
- **`cli`** (optional) — a backend override (currently only `opencode`).
- **`model`** (optional) — a pinned model (e.g. a reviewer pinned to a different
  model than the fixer for intentionally different review output).
- **`personality_md`** — markdown merged into the task worktree's `AGENTS.md`.
- **`skills`** — a list of `{name, content}` markdown files.
- **`custom_instructions`** — text appended to the task prompt when selected.
- **`enabled`** — disabled agents are not selectable on new tasks.

## 2. Storage

The catalog lives in the `catalog_agents` table (see `docs/02`). Skill content
is stored **in the DB** as JSON (`skills_json`) — the DB is the single source of
truth. At run time the queue materializes the skills **directly into the task
worktree** (`.claude/skills/<name>/SKILL.md`, see §3); nothing is written to
`<data-dir>/agents/` (the directory exists for future use but is not part of the
run path).

## 3. How the orchestrator applies an agent (PRD F6.2)

When a task is created with an `agent_id`:

1. **At creation** (`routes/tasks.py` → `tasks.create_task`): the slug is
   validated to exist and be enabled. No cli/model snapshot is taken — the
   task's own `cli`/`model` (if any) are **explicit user overrides only**.
2. **At run time** (`queue.py` `_run_task`/`_run_review`/`_run_followup`):
   - `_catalog_agent()` re-resolves the agent from the DB. A task whose agent
     was deleted/disabled **after creation** falls back to the default build
     agent rather than failing.
   - Precedence is **explicit task override > live agent pin > default** for
     BOTH `cli` and `model` (`_agent_run_opts` / `effective_model`). Editing an
     agent after creation therefore changes behavior for any task that didn't
     explicitly override.
   - `custom_instructions` is appended to the effective prompt.
   - `prompts.build_agent_md(task, repo, agent=...)` merges `personality_md`
     into a dedicated `## Agent personality` section and lists each skill as an
     `@path` reference (`@.claude/skills/<name>/SKILL.md`).
   - `worktree_bootstrap.bootstrap_worktree(..., skills=...)` materializes the
     skills into `.claude/skills/<name>/SKILL.md` in the worktree — opencode's
     native skills loading (PRD F6.4; `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`
     stays unset). A changed skill list prunes stale subdirs so a rerun matches
     the catalog. The default build agent reads `AGENTS.md` + skills and uses
     them opportunistically.

**No special prompts plumbing** — the personality + skills mechanism is files in
the worktree, so it works identically for opencode (today) and Codex/Claude
later (the adapter only varies the file conventions).

## 4. `tasks.agent_id` is FK-less by design

`tasks.agent_id` is a plain nullable text column referencing `catalog_agents.id`
by slug — **no database FK**. Adding one would require a SQLite batch rebuild of
the `tasks` table (a parent FK-referenced by `runs`/`followups`), which is the
exact failure mode documented in Step 37 (`PRAGMA foreign_keys` cannot be turned
off inside an Alembic transaction). Validity is enforced in the service layer:

- Creation refuses unknown/disabled agents.
- Run time re-resolves and falls back gracefully if the agent vanished.
- Deleting an agent leaves task history intact (the slug stays on historical
  tasks; a rerun simply uses the default build agent).

## 5. API

`/api/agents` (see `routes/catalog.py`):

- `GET /api/agents` — list (all, or `?enabled=1` for pickers).
- `POST /api/agents` — create (validates id slug, name, kind, cli, model,
  personality, skills, custom instructions, enabled). Returns `201`.
- `GET /api/agents/<slug>` — fetch one.
- `PUT /api/agents/<slug>` — partial update (missing fields keep their values).
- `DELETE /api/agents/<slug>` — delete (tasks keep history).

## 6. UI

The **Agents** page (top-bar nav) is the catalog editor: create/edit/delete
agents with personality textarea, skills editor (add/remove markdown files),
model/CLI pins, custom instructions, and the enabled toggle.

The **New Task** form gains an **Agent** picker (default build agent or an
enabled catalog agent). Selecting an agent prefills the model from the agent's
pin (user can still override). Tasks created with an agent carry `agent_id` and
resolve the rest at run time.

## 7. Verification

- `tests/test_catalog.py` — service CRUD, slug/definition/skills validation,
  enable-only listing, file materialization, task-create integration.
- `tests/test_api_catalog.py` — route create/list/get/update/delete + task
  creation with an agent (pinned cli/model become defaults).
- `tests/test_prompts.py` — personality + `@path` skill links merged into
  `AGENTS.md`.
- `tests/test_worktree_bootstrap.py` — skills written to
  `.claude/skills/<name>/SKILL.md`, cleared when unused, kept out of `git add`.
- `tests/test_queue.py` — run time applies cli/model/custom_instructions/skills;
  a disabled-after-creation agent falls back to defaults.
- `apps/web/src/pages/Agents.test.tsx` — page list/create/edit/delete.

See `docs/09` for how to run the suites.
