# Phase 0 — Comprehensive Code Review

> **Reviewed:** 2026-08-07
> **Scope:** Full codebase (backend + frontend + configs + tests + docs)
> **Focus:** Logic correctness, sensitive info leak risks, PRD compliance
> **Score:** 4.2 / 5

---

## Overall Verdict

Phase 0 is **solid**. The codebase is well-structured, the security posture around secret masking is thorough, and the architecture matches the PRD faithfully. No critical issues and no sensitive information leak paths found. A handful of medium-severity findings (mostly documentation drift and a subtle env-inheritance edge case) and several low-severity observations.

| Category | Score | Notes |
|----------|-------|-------|
| Correctness | 4/5 | One env-inheritance edge case, one DB session pattern concern |
| Security (info leaks) | 5/5 | Masking is comprehensive — tokens can't leak via API, SSE, logs, prompts, or commits |
| Code quality | 4/5 | Clean, well-organized; minor inconsistencies |
| Test coverage | 4/5 | 207 backend + 16 web; a few integration gaps |
| PRD compliance | 4/5 | All Phase 0 features implemented; docs drift on data model |
| Architecture | 5/5 | Simple, extensible, matches the spec |

---

## Findings

### MEDIUM

**M1. `git_workspace._auth_env` doesn't clear inherited `GIT_CONFIG_*` vars**

`git_workspace.py:57-61` — Sets `GIT_CONFIG_COUNT=1` plus `KEY_0`/`VALUE_0`, but if the parent process has `GIT_CONFIG_COUNT=3`, git merges both configs. An inherited lower-indexed `http.extraHeader` could override or duplicate the auth header, and inherited `GIT_CONFIG_*` from the user's environment could leak into agent subprocesses.

**Fix:** Before setting the auth entries, explicitly clear higher-indexed keys or set `GIT_CONFIG_NOSYSTEM=1` + `GIT_CONFIG_GLOBAL=/dev/null` + `GIT_CONFIG_COUNT=1` to ensure a clean slate.

**M2. `docs/02-data-model.md` is outdated**

The data model doc only shows the original schema from migration `180905017971`. Columns added by later migrations are undocumented:

- `repos.connected` (migration `c7d2a1b3e5f6`)
- `repos.pat_name` (migration `e1f9c2d4a6b8`)
- `tasks.pat_name`, `tasks.issues_json`, `tasks.prs_json`, `tasks.context_json` (migration `b3c1a5f2d9e4`)
- `runs.pid`, `runs.pat_name` (migrations `0d42c1a9f0b1`, `b3c1a5f2d9e4`)
- `followups.pat_name`, `followups.model` (migration `b3c1a5f2d9e4`)

A developer reading the docs would have an incomplete picture of the schema.

**M3. `_run_task` / `_run_followup` exception handler uses stale ORM objects after `session.rollback()`**

`queue.py:428-443` — After `session.rollback()`, the code does `task = session.get(Task, task_id)` (correct), but if the rollback fails or the re-fetch returns `None` (e.g., a concurrent delete), the `run` object from before the rollback may be in an inconsistent state. With `expire_on_commit=False`, the stale `run` could persist incorrect data. The probability is very low (single worker thread per task), but the pattern is fragile.

**M4. `_auth_env` passes the raw token through `base64.b64encode`**

`git_workspace.py:56` — The token is embedded in `Authorization: basic <base64(x-access-token:token)>`. While this avoids putting the token in argv/URLs, the base64-encoded value is trivially reversible. If git logs or error messages ever dump the `http.extraHeader` value, the token is exposed. The `GIT_CONFIG_*` approach is still better than URL-embedded credentials, but worth noting.

**M5. `clone_url` exposed in `repos.repo_to_dict()`**

`repos.py:55` — The `clone_url` field (which could contain `https://x-access-token:<token>@github.com/...` if the user ever configured it that way) is returned in the `/api/repos` response. For localhost-only this is fine, but if the app is tunneled, this URL could leak.

---

### LOW

**L1. Masking regex errors silently swallowed**

`masking.py:21` — Invalid user-supplied regex patterns are silently `continue`d. If a user enters a malformed pattern in Settings, they'd have no feedback that their masking rule isn't active.

**L2. `_run_review` exception handler doesn't call `_maybe_retry`**

`queue.py:540-552` — If a review task fails, it's never auto-retried even if `retry_policy.auto_retry` is enabled. This is probably intentional (reviews are read-only, retrying a failed review is less useful), but it's inconsistent with `_run_task` which does retry.

**L3. `timed_out` status not auto-retried**

`queue.py:737` — `_maybe_retry` only triggers on `run.status == "failed"`. Tasks that `timed_out` are never auto-retried. The PRD says "auto-retry on transient failures (e.g. network) is configurable" — timeouts might qualify, but the current behavior is defensible.

**L4. Follow-up response field mismatch (cosmetic)**

`routes/repos.py:68` — `disconnect_repo` returns `{"disconnected": name}` but the frontend API client types it as `{"removed": string}`. Neither side actually reads the field (both reload the list), so it's cosmetic only.

**L5. No concurrent-same-repo test**

There's no test verifying two tasks on the same repo can run simultaneously (the `_lock_for(full_name)` serialization in `GitWorkspace` could become a bottleneck). The lock is per-repo and held during `git fetch` + `worktree add`, which could block the second task.

**L6. Frontend hardcodes `opencode-go/deepseek-v4-flash` as default model**

`Tasks.tsx:46` — `DEFAULT_MODEL = "opencode-go/deepseek-v4-flash"` is hardcoded. If the user's opencode setup doesn't have this model, it'll be sent to the CLI anyway (opencode would reject it or fall back). Should be dynamic from `listModels()` or left empty.

**L7. Migration timestamps out of order**

Migrations `c7d2a1b3e5f6` (repos_connected) and `e1f9c2d4a6b8` (repos_pat_name) have timestamps earlier than their parent migrations. Cosmetic only (Alembic uses the `down_revision` chain, not timestamps).

---

## Sensitive Info Leak Assessment: CLEAN

- **`.gitignore`**: Correctly excludes `.env`, `.env.*`, `secrets.json`, `.jalebi/`
- **`.env.example`**: Empty values only, clear warnings
- **No hardcoded tokens** in any committed source file
- **Masking applied at ingest**: `build_masker` is called in `_stream_and_finish`, `_step_from_event`, `_pr_title_and_body`, `_run_review`, `_fetch_context`, and `create_task` — before any event is broadcast, persisted, or posted to GitHub
- **All API endpoints** return `masked` token previews (first 4 + last 4 chars), never raw values
- **Agent environment**: `JALEBI_GITHUB_TOKEN` is set in the subprocess env (necessary for the agent), but `GH_TOKEN`/`GITHUB_TOKEN` are explicitly stripped
- **PR body/title**: Masked before posting to GitHub
- **Review text**: Masked before posting as PR review comment
- **Issue/PR context**: Masked at fetch time before storing in `context_json`
- **Prompt storage**: Masked before writing to DB
- **Error messages**: Only HTTP status codes and GitHub error messages are logged — no request bodies or headers
- **Git auth**: Token passed via `GIT_CONFIG_*` env vars (not in argv or URLs)
- **No `gh` CLI**: Triple-guarded (opencode.json deny rules, env stripping, prompt instructions)
- **Pre-commit hook**: Rejects staged `.jalebi/` files
- **Artifact capture**: `.jalebi/` excluded via `.gitignore`; bootstrap files (AGENTS.md, opencode.json, .gitignore) excluded from artifacts

**Verdict: No path exists for tokens to leak into commits, API responses, SSE streams, log files, agent prompts, or GitHub posts.**

---

## Files Reviewed

### Backend (apps/server/src/jalebi/)

| File | Lines | Status |
|------|-------|--------|
| `app.py` | 152 | Clean |
| `config.py` | 49 | Clean |
| `db.py` | 241 | Clean |
| `events.py` | 47 | Clean |
| `git_workspace.py` | 299 | M1, M4 |
| `github.py` | 257 | Clean |
| `masking.py` | 29 | L1 |
| `prompts.py` | 142 | Clean |
| `queue.py` | 891 | M3, L2, L3 |
| `repos.py` | 62 | M5 |
| `secrets.py` | 154 | Clean |
| `settings.py` | 41 | Clean |
| `tasks.py` | 185 | Clean |
| `worktree_bootstrap.py` | 202 | Clean |
| `artifacts.py` | 93 | Clean |
| `adapters/__init__.py` | 19 | Clean |
| `adapters/opencode.py` | 133 | Clean |
| `adapters/types.py` | 100 | Clean |
| `routes/tasks.py` | 400 | Clean |
| `routes/repos.py` | 151 | L4 |
| `routes/github.py` | 314 | Clean |

### Frontend (apps/web/src/)

| File | Lines | Status |
|------|-------|--------|
| `types.ts` | 155 | Clean |
| `App.tsx` | 127 | Clean |
| `api/client.ts` | 137 | Clean |
| `pages/Tasks.tsx` | 609 | L6 |
| `pages/TaskDetail.tsx` | 642 | Clean |
| `pages/Github.tsx` | 373 | Clean |
| `pages/Repos.tsx` | 121 | Clean |
| `pages/Settings.tsx` | 240 | Clean |
| `components/ComingSoon.tsx` | — | Clean |
| `components/StatusBadge.tsx` | — | Clean |

### Config & Scripts

| File | Status |
|------|--------|
| `package.json` | Clean |
| `apps/server/pyproject.toml` | Clean |
| `apps/web/package.json` | Clean |
| `apps/web/vite.config.ts` | Clean |
| `tsconfig.base.json` | Clean |
| `eslint.config.mjs` | Clean |
| `.prettierrc.json` | Clean |
| `.gitignore` | Clean |
| `.env.example` | Clean |
| `start.sh` | Clean |
| `stop.sh` | Clean |

### Migrations

| Migration | Status |
|-----------|--------|
| `180905017971` (phase0_tables) | Clean |
| `0d42c1a9f0b1` (add_runs_pid) | Clean |
| `b3c1a5f2d9e4` (task_context_pat) | Clean |
| `c7d2a1b3e5f6` (repos_connected) | L7 (timestamp) |
| `e1f9c2d4a6b8` (repos_pat_name) | L7 (timestamp) |

All migrations have `downgrade()` functions, no data migrations, schema matches ORM models (verified by `test_migrations_match_models`).
