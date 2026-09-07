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
- **`skills`** — legacy inline one-off `{name, content}` markdown files (stored
  on the agent; the form still supports them for agent-specific extras).
- **`skill_ids`** — ordered links into the shared skill library (reference
  semantics: a library edit propagates to every linked agent at run time).
- **`description`** — one-liner shown on cards and in pickers.
- **`avatar`** — profile picture id (`apps/web/public/avatars/*.svg`); NULL =
  auto-assign (keyword match on name+description, hash fallback).
- **`enabled`** — disabled agents are not selectable on new tasks.
- **`custom_instructions`** — text appended to the task prompt when selected.

## 2. Storage

The catalog lives in two tables (see `docs/02`):

- **`catalog_skills`** — the standalone skill library (`id` slug PK, name,
  description, markdown `content` ≤100 KB, `tags_json`, timestamps).
- **`catalog_agents`** — agents with `skill_ids_json` (ordered library links,
  FK-less by design) plus legacy inline `skills_json` extras.

The DB is the single source of truth. At run time the queue resolves
`resolve_skills()` (library in link order, then inline extras deduplicated by
name with the library winning; unknown slugs skipped) and materializes the
result into the task worktree (`.claude/skills/<name>/SKILL.md`, see §3);
nothing is written to `<data-dir>/agents/` (the directory exists for future
use but is not part of the run path). The `d4e5f6a7b8c9` migration moved
pre-library inline skills into the library (deduplicated; same name +
different content got `-2` suffixes) and cleared `skills_json`.

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
the worktree, so it works identically for opencode, Codex, and Claude Code (the
adapter only varies the file conventions: AGENTS.md for opencode/codex, plus
CLAUDE.md for claude).

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

- `GET /api/agents` — list (all, or `?enabled=1` for pickers). Each agent
  carries resolved `skills` (library + inline), `skill_ids`, `description`,
  and `avatar`.
- `POST /api/agents` — create (validates id slug, name, kind, cli, model,
  personality, skills, skill_ids, custom instructions, enabled, description,
  avatar). Unknown `skill_ids` are a 400. Returns `201`.
- `GET /api/agents/<slug>` — fetch one.
- `PUT /api/agents/<slug>` — partial update (missing fields keep their values).
  Switching `cli` without a new `model` drops the stale model pin; `enabled`
  must be a real boolean; text fields must be strings; `""` avatar clears back
  to auto.
- `DELETE /api/agents/<slug>` — delete (tasks keep history).
- `GET /api/agents/<slug>/usage` — where the agent is referenced: historical
  task count + live trigger rules (so the UI warns before a delete that would
  break rules at dispatch).

`/api/skills` (see `routes/skills.py`):

- `GET /api/skills` — list the library (search/filter/sort client-side).
- `POST /api/skills` — create `{id, name, description, content, tags}`.
- `GET /api/skills/<slug>` — fetch one.
- `PUT /api/skills/<slug>` — partial update (linked agents pick it up at run
  time — no fan-out).
- `DELETE /api/skills/<slug>` — refused with **409 + linking agents** while
  linked; unlink first.
- `GET /api/skills/<slug>/usage` — linking agents (pre-delete warning).

## 6. UI

The **Agents** page (top-bar nav) is the catalog editor: avatar (auto-suggested
from name+description via `lib/agentAvatars.ts` keyword map with a hash
fallback; 12 built-in SVGs in `public/avatars/`, overridable in the picker),
description, personality textarea, **library skill attach picker** (checkboxes,
reference semantics) + inline one-off skills editor, model/CLI pins (model
resets on CLI switch), custom instructions (with 20k cap counter), slug format
guidance, kind explanations, and the enabled toggle. Rows show the avatar,
description, usage (task count + trigger rules), an enable/disable toggle, and
a delete confirm that names impacted rules. Editing agent B with the form open
on A remounts via `key`.

The **Skills** page (top-bar nav) manages the library: search (id/name/
description/content), tag filter chips, sort (name / recently updated / most
used), cards (description, tags, "linked by N agents"), create/edit form (slug,
name, description, comma tags, markdown body with 100k counter), delete with a
confirm naming linked agents (the server 409s while linked).

The **New Task** form gains an **Agent** picker (default build agent or an
enabled catalog agent). Selecting an agent prefills the model from the agent's
pin (user can still override). Tasks created with an agent carry `agent_id` and
resolve the rest at run time.

## 7. Verification

- `tests/test_catalog.py` — service CRUD, slug/definition/skills validation,
  enable-only listing, file materialization, task-create integration.
- `tests/test_catalog_skills.py` — library CRUD + validation, link validation,
  resolve order/merge/unknown-skip, reference propagation, usage + delete
  guard, description/avatar validation.
- `tests/test_api_catalog.py` — route create/list/get/update/delete + task
  creation with an agent (pinned cli/model become defaults).
- `tests/test_api_skills.py` — skill routes CRUD/validation, 409-with-agents
  delete, usage, agent link/meta fields + unknown-link 400s.
- `tests/test_seed_catalog.py` — insert-then-noop, never-overwrites,
  deletion-stays-deleted, version-bump delivers.
- `tests/test_prompts.py` — personality + `@path` skill links merged into
  `AGENTS.md`.
- `tests/test_worktree_bootstrap.py` — skills written to
  `.claude/skills/<name>/SKILL.md`, cleared when unused, kept out of `git add`.
- `tests/test_queue.py` — run time applies cli/model/custom_instructions/skills;
  a disabled-after-creation agent falls back to defaults.
- `apps/web/src/pages/Agents.test.tsx` — page list/create/edit/delete, usage
  display + row toggle, stale-form remount, inline slug/skill validation,
  model reset on CLI switch, delete-impact confirm, avatar/description rows,
  avatar suggestion + library attach + explicit pick.
- `apps/web/src/pages/Skills.test.tsx` — list with usage, search, tag filter,
  most-used sort, create with tags, slug validation, delete-impact confirm,
  stale-form remount.

See `docs/09` for how to run the suites.

## 8. Seed content

`jalebi/seed_catalog.py` holds curated starter skills/agents (`SEED_SKILLS` /
`SEED_AGENTS`) + `seed_catalog(session)`, called from `create_app` after
`seed_defaults`. Insert-missing only, gated by the `catalog_seed_version`
setting: bumping `SEED_VERSION` delivers new seeds; owner edits are never
overwritten and deletions never return within a version. Seed bodies are
Jalebi-authored (inspired by public skill collections, never vendored
verbatim — note the CC-BY-SA share-alike trap on trailofbits-sourced ideas).

v1 ships **31 skills** (18 general software + academic-writing + 12 domain:
mobile ×4, academic review, ML/data ×5, SRE, CLI writing, wrangling safety)
and **16 agents** (9 general + android/ios engineers, academic-reviewer,
ml-experimenter, model-auditor, incident-commander, issue-writer). The list
was curated from two independent research surveys plus a Codex second opinion
and passed an AGY quality review (implausible links fixed, overlaps trimmed,
every skill linked by at least one agent, uniform `## Verification`
sections). Bodies live in `seed_data_skills.py` / `seed_data_agents.py`
(`# ruff: noqa: E501` — prose lines); `test_seed_content.py` pins counts and
validates every entry through the service validators.
