# 09 — Testing

> **Scope:** Test strategy per layer, how to run tests, and fixtures. Update this file for any test infrastructure changes.

---

## 1. How to run tests

- Server tests: `uv run pytest` (inside `apps/server`).
- Web tests: `npm test -w @jalebi/web` (vitest).
- Both: `npm test` (runs server pytest then web vitest).
- Lint/format: `uv run ruff check` (server); `npm run lint` / `npm run format` (web).
- Typecheck: `npm run typecheck` (web, `tsc --noEmit`).
- Build: `npm run build` (web → `apps/web/dist`, served by Flask).

After any code change, run the relevant tests and build before marking work done. Existing tests take precedence: if a change breaks a test, fix the code, not the test (unless the user explicitly asks to change the test).

## 2. Test layout

- **Server (`apps/server/tests/`, pytest):** `test_health`, `test_config`, `test_schema` (migrations match models via `compare_metadata`), `test_settings`, `test_api_settings`, `test_secrets`, `test_github` (client), `test_api_github`, `test_repos`, `test_api_repos`, `test_git_workspace` (local git repos, incl. real `refs/pull/N` review worktree), `test_opencode_adapter`, `test_runhandle`, `test_masking`, `test_prompts`, `test_worktree_bootstrap`, `test_artifacts`, `test_queue`, `test_followups`, `test_reliability`, `test_api_tasks`, `test_sse`, `test_spa`. Fixtures in `conftest.py`: `config` (tmp data dir), `app` (via `create_app(config)`), `client` (Flask test client), `session`, `engine`. (~252 tests; `uv run pytest`.)
- **Web (`apps/web/src`, vitest + RTL):** `App.test.tsx` (shell), `pages/Tasks.test.tsx`, `pages/TaskDetail.test.tsx`, `pages/Github.test.tsx`, `pages/Settings.test.tsx`. (~19 tests; `npm test -w @jalebi/web`.)

## 3. Test strategy per layer (PRD §13)

| Layer | Strategy |
|-------|----------|
| **Adapters** | Unit-tested with **mocked/real CLI output** — `parse()` is fed real captured `opencode --format json` lines; command construction via monkeypatched `_spawn`; `RunHandle` with a fake proc (in-memory stdout/stderr). |
| **Queue** | Tested with **fake agents** (no real CLI) + local throwaway git repos + monkeypatched `GitHubClient`; asserts success/failure/timeout/cancel/queued-skip/publish/publish-failure/exception-finalizes-run. |
| **GitHub client** | `_request` seam monkeypatched with canned `(status, body, headers)` — no real GitHub calls in unit tests. |
| **Tasks API** | Flask test client against a temp-db app; mocked no network. |
| **SSE** | `TaskEvents` bus unit tests + endpoint integration test using `client.get(..., buffered=False)` (werkzeug buffers streaming responses by default) driven from a reader thread. |
| **SPA serving** | Flask test client: `/` serves `index.html`, client-route fallback, assets 200, `/api` unaffected, 503 when the build is missing. |
| **Git workspace mgr** | Tested against local throwaway bare repos (init, mirror clone + incremental fetch, worktree add/remove, branch naming, push to a local remote). Auth asserted via mocked `_run_git` (token only in `GIT_CONFIG_*` env, never argv). |
| **Secret masking** | Tested at the ingest layer — assert PAT/user regex patterns are redacted before broadcast/persist. |

## 4. Fixtures

- Adapter parse fixtures: **real captured** `opencode v1.18 --format json` lines (`step_start`, `tool_use`, `text`, `step_finish`, `error`) in `tests/test_opencode_adapter.py`.
- Local git remote fixture: `git init --bare` + seeded `main` commit (shared pattern across `test_git_workspace.py`, `test_queue.py`, `test_sse.py`).
- Fake agents: `FakeHandle`/`BlockingHandle` (in-memory procs) for queue/SSE tests.

## 5. Testing against GitHub (integration)

- **All GitHub interaction for testing goes through `JALEBI_GITHUB_TOKEN`** (via the app's httpx client, `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"`, or git with the token) — **never `gh`** (PRD §17.2, AGENTS.md §3.3).
- The token is for a **dedicated testing repo/account**: `example-account` (test repos include **`example-smoke-repo`**, created for Jalebi smoke tests). Never point tests at production/user repos.
- Manual smoke tests (e.g. end-to-end task → PR) run against `example-smoke-repo` and are cleaned up afterwards (PR closed, branch deleted).
- The `gh` CLI is authorized **only** for local git operations on the Jalebi repo itself (staging, committing, pushing to `Rishabh-Bajpai/Jalebi`) — never for testing/verification against the testing account.

## 6. Reference

- PRD §13 (non-functional requirements — testability), §17.2 (no `gh` CLI), §17.3 (testing repo & account).
