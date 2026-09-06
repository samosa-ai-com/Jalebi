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

---

## 8. Phase 4 T1.1 — env-var block-list

`ENV_BLOCK_LIST` (frozenset) and `ENV_BLOCK_PREFIXES` (tuple of name prefixes) in `envvars.py` define names that MUST NOT appear in the agent subprocess environment, regardless of what the store contains.

**Block-list contents** (`jalebi.envvars.is_blocked_env_name`):
- Process / shell control: `PATH`, `HOME`, `LD_PRELOAD`, `NODE_OPTIONS`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `NODE_TLS_REJECT_UNAUTHORIZED`, `PYTHONPATH`, `PYTHONSTARTUP`, `PYTHONINSPECT`, `GIT_SSH_COMMAND`, `GIT_SSH_VARIANT`, `GIT_ASKPASS`, `SSH_ASKPASS`.
- Jalebi's own secrets / identity: `JALEBI_GITHUB_TOKEN`, `JALEBI_DATA_DIR`, `JALEBI_PORT`, `JALEBI_HOST`, `JALEBI_PASSWORD`.
- GitHub CLI / API creds: `GH_TOKEN`, `GITHUB_TOKEN`.
- git env control: `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_CEILING_DIRECTORIES`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_CONFIG_NOSYSTEM`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`.
- Prefix: every name starting with `GIT_CONFIG_` (block-all — see rationale below).

**Rationale (the override hole it closes):** `_agent_env` (`queue.py:374-376`) merges the task's stored env vars **on top of** the Jalebi-built env (never the other way around), so a task cannot override the token/identity/git hygiene the queue pins. The blocklist is the belt-and-suspenders: even if a row was inserted via a direct DB write (a legacy row, a future bug, or a misconfigured import), `values_for_names` filters blocked names before the dict reaches `_agent_env.update(...)`. Per-name at DEBUG; a single WARNING summary per `values_for_names` call when anything was dropped.

**`GIT_CONFIG_*` block-all rationale:** the `GIT_CONFIG_*` namespace is the entire mechanism by which git injects arbitrary config into the subprocess; Jalebi pins `GIT_CONFIG_NOSYSTEM=1` / `GIT_CONFIG_GLOBAL=devnull` in `_build_agent_env` and never exposes push creds to the agent. Any stored `GIT_CONFIG_*` would either re-inject credential/`url.insteadOf` state or override Jalebi's pins. No legitimate user surface — block-all via the prefix tuple.

**Write-time:** `upsert_env_var` raises `ValueError(f"env var name is blocked: {name}")` for any blocked name; the existing route maps `ValueError` to 400 with a clear message.

**Import-time:** `import_env_file` (the `.env` POST endpoint) skips blocked lines and appends `<line> (blocked)` to the `skipped` list returned by the route — the file is partially imported and the user sees what was ignored.

**Read-time:** `values_for_names` drops blocked names from the resolved dict before it is merged into the agent env by `_agent_env`. This is the critical choke point — it covers legacy rows that pre-date the blocklist.
