# 14 — Environment variables for task agents

> **Scope:** The `env_vars` store (global + per-repo variables injected into task agents), the `/api/envvars` routes, `.env` import, and how they reach the agent + masking.

**Implementation:** `src/jalebi/envvars.py` (service), `src/jalebi/routes/envvars.py` (API), `env_vars` table + `tasks.env_vars_json` (migration `d9e8f7c6b5a4`). Tests in `tests/test_envvars.py`, `tests/test_queue.py`.

---

## 1. Why

A repo often needs environment variables to build/test/develop (DB URLs, API keys, tokens). Without them the agent can't run the very commands a task requires. Jalebi lets the owner store variables once and **select which ones a task gets**.

## 2. Data model

- **`env_vars` table:** `id`, `name`, `value`, `repo_id` (**NULL = global**; non-NULL = scoped to one repo), `created_at`, `updated_at`. Unique per `(name, repo_id)` — a name can be defined globally *and* per-repo.
- **`tasks.env_vars_json`:** a JSON list of **names** the task's creator selected. Resolution: repo-scoped value wins over a global value of the same name; globals fill everything the repo doesn't override.

## 3. Security: values are secrets

- The API **never returns a value in full** — only `{ id, name, masked, repo_id, repo_full_name, created_at }` with a short preview (`DATABASE_URL → "postg***b"`). Editing = re-submit the value.
- At task run/follow-up start, the selected values are added to the **masker secret list**, so if the agent echoes a value in output, a step, an artifact, or a diff, it is redacted (`***`).
- Values are injected into the agent subprocess **env**, merged on top of the Jalebi-pinned env — they can never override `JALEBI_GITHUB_TOKEN`, git identity, or the gh/git hygiene vars.

## 4. API

| Endpoint | Behavior |
|---|---|
| `GET /api/envvars?repo_id=` | List env vars (globals + that repo's), masked. |
| `POST /api/envvars` | `{name, value, repo_id?}` → upsert (by name+scope). 400 on invalid name (spaces/`=`). |
| `POST /api/envvars/import` | `{content, repo_id?}` → parse `.env` text (`KEY=VALUE`, `export KEY=`, quotes, comments) and upsert each; returns `{imported, env_vars}`. |
| `DELETE /api/envvars/<id>` | Remove a variable. |

## 5. Task integration

- The new-task form shows a **checkbox chip** per applicable variable (global + selected repo's); checked ones are sent as `env_vars: [names]` and stored in `tasks.env_vars_json`.
- `TaskQueue._agent_env` resolves the selected names (`envvars.values_for_names`) and merges them into the subprocess env for `start` and `resume` (follow-ups inherit the task's selection).
- The same values feed the **masker** at run start, so they're redacted if echoed.

## 6. UI

- **Settings → Environment variables:** add/edit/delete (per-repo scope dropdown), a **paste-a-.env + Import** box, and a masked list with scope badges.

## 7. Reference

- PRD §F17 (masking) — env-var values are treated as secrets at ingest.
- Related: `docs/03-adapters.md` (agent env), `docs/10-security.md`.
