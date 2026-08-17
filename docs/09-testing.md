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

- **Server (`apps/server/tests/`, pytest):** `test_health`, `test_config`, `test_clock` (the app wall clock: local default, IANA override, offset-suffixed serialization), `test_schema` (migrations match models via `compare_metadata`), `test_settings` (defaults, round-trip, cross-session persistence, **`seed_defaults` materializes all keys at startup without overwriting user values**, full-restart persistence), `test_api_settings` (incl. merged `ntfy_topic` validation, notify toggles, removed `ntfy_url`), `test_secrets` (strict no-fallback resolution), `test_github` (client), `test_api_github` (equal-account model), `test_repos`, `test_api_repos` (connect requires an account), `test_git_workspace` (local git repos, incl. real `refs/pull/N` review worktree, stale-branch reset, prune of missing-but-registered worktrees, and publish-time `merge_origin_into` clean + conflict-abort + no-merge-in-progress raise), `test_opencode_adapter` (incl. no server-env leak in `_spawn`), `test_codex_adapter` (parse mapping incl. the notices-not-fatal invariant, `turn.failed` JSON unwrap, sandbox-probe both branches, **sandbox fallback logs a one-time warning**, models-cache read incl. **`supported_in_api` preference**, spawn wrapper), `test_claude_adapter` (no-auth `is_error` discriminator, init session capture, `result` terminal mapping, spawn wrapper), `test_runhandle`, `test_masking`, `test_prompts` (incl. **catalog personality + `@path` skill links**), `test_worktree_bootstrap` (incl. `external_directory: deny` guard, **per-CLI gh-guards — codex `.codex/rules/default.rules` (with `match`/`not_match` self-tests) + claude `.claude/settings.json` + the `PreToolUse` external-dir hook (`.claude/hooks/jalebi_deny_external.py`, unit-tested with hook JSON payloads), `CLAUDE.md` write for claude runs, all three guards skip repo-owned files (no-clobber) + remove_guard content-match cleanup, pre-commit hook rejects staged claude settings/skills, agent skills materialized to all three roots (`.claude`/`.codex`/`.agents`)/pruned when unused/excluded from git add**), `test_catalog` (service CRUD, slug/definition/skills validation, enable-only listing, file materialization), `test_api_catalog` (routes + task-creation with an agent), `test_reviews` (assign → one pr_review task per reviewer, kind/enabled validation, default prompt, status transitions, assignment dict, dedupe + partial-cleanup), `test_api_reviews` (assign endpoint, reviewers-on-create, kind validation, no-PR 409, address-reviewers follow-up embeds masked reviews, pr_number-column fallback), `test_webhooks` (signature verify, event-context extraction, rule CRUD + branch/author/label matching, delivery dedup, dispatch for start_review/triage_issue/create_task/rerun_review), `test_api_webhooks` (POST /webhook end-to-end incl. dedup + signature 403 + unconnected-repo ignore + **write-only webhook_secret + password-requires-secret + triage masking + concurrent redelivery race**, /api/triggers CRUD + validation, webhook status, replay-with-dedup, registration-without-URL 409, `/api/models?cli=` per-backend + unknown→empty + override), `test_artifacts`, `test_queue` (incl. selected-account agent env, dirty-tree surfacing, per-task publish mode, issue_fix single-target worktree base, publish conflict → needs_approval, issue comment on new-PR only, manual-publish no-op gate, **terminal + progress ntfy notifications, env-var injection into agent env, env-var value masking, catalog agent cli/model/custom-instructions/skills applied at run time + disabled-agent fallback, reviewer assignment lifecycle (posted on success, failed on posting error / non-done run)**), `test_cron` (5-field matcher: single/list/range/step values, invalid expressions, `datetime` wrapper + day-of-week shift), `test_screening` (findings parse incl. fenced/prose/bad input + string-literal bracket handling + severity normalization, run loop with fake adapter over a real git remote, baseline dedup + force, no-account failure, findings/output masking, error-event → failed, ntfy on findings when enabled / skipped when disabled, CRUD validation, scheduler `_due_screens` + `tick` with a fixed clock, unpinned screen resolves `default_backend` while a pinned one wins, default model applied only on the default backend), `test_api_screening` (templates, CRUD + validation, runs history, async run-now), `test_check_runs` (state mapping, status context, type+flag gating, pr_review pending→terminal on the PR head, issue_fix status set completed at publish, status API failure is non-fatal), `test_api_repos` (incl. `PATCH /api/repos/<id>` check_runs_enabled toggle + validation), `test_followups` (incl. follow-up backend override → fresh-session fork seeded with the prior conversation; same-backend resumes), `test_reliability`, `test_api_tasks` (incl. publish-mode defaults/override, oversized-prompt rejection, delete, account required, publish 409 on conflict/no-op, `env_vars` on create), `test_envvars` (service + routes: upsert/scope/`.env` import incl. empty/multiline/skipped-line reporting/masked API), `test_notify` (endpoint parsing, JSON publishing to server root, masked send, silent failure), `test_api_notify` (test endpoint), `test_sse`, `test_spa`. Fixtures in `conftest.py`: `config` (tmp data dir), `app` (via `create_app(config)`), `client` (Flask test client), `session`, `engine`. (~629 tests; `uv run pytest`.))
- **Web (`apps/web/src`, vitest + RTL):** `App.test.tsx` (shell), `pages/Tasks.test.tsx`, `pages/TaskDetail.test.tsx` (incl. run-end diff rendering with file label + stats, run history rows with status/duration/markers), `pages/Github.test.tsx`, `pages/Settings.test.tsx`, `pages/Repos.test.tsx` (incl. the check-runs toggle), `pages/Screenings.test.tsx` (incl. template-selection visibility, scope-branch dropdown, cron presets, edit-from-card, the in-flight "Running…" state, and the UNTRUSTED finding→task prompt),            `pages/Agents.test.tsx` (incl. the CLI override + model-pin dropdown options). (~63 tests; `npm test -w @jalebi/web`.)

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

- Adapter parse fixtures: **real captured** `opencode v1.18 --format json` lines (`step_start`, `tool_use`, `text`, `step_finish`, `error`) in `tests/test_opencode_adapter.py`. `codex`/`claude` parse fixtures (JSONL lines for `thread.started`/`turn.failed`/`result` subtypes) live in `tests/test_codex_adapter.py` / `tests/test_claude_adapter.py`.
- Local git remote fixture: `git init --bare` + seeded `main` commit (shared pattern across `test_git_workspace.py`, `test_queue.py`, `test_sse.py`).
- Fake agents: `FakeHandle`/`BlockingHandle` (in-memory procs) for queue/SSE tests.

## 5. Testing against GitHub (integration)

- **All GitHub interaction for testing goes through `JALEBI_GITHUB_TOKEN`** (via the app's httpx client, `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"`, or git with the token) — **never `gh`** (PRD §17.2, AGENTS.md §3.3).
- The token is for a **dedicated testing repo/account**: `example-account` (test repos include **`example-smoke-repo`**, created for Jalebi smoke tests). Never point tests at production/user repos.
- Manual smoke tests (e.g. end-to-end task → PR) run against `example-smoke-repo` and are cleaned up afterwards (PR closed, branch deleted).
- The `gh` CLI is authorized **only** for local git operations on the Jalebi repo itself (staging, committing, pushing to `Rishabh-Bajpai/Jalebi`) — never for testing/verification against the testing account.

## 6. Reference

- PRD §13 (non-functional requirements — testability), §17.2 (no `gh` CLI), §17.3 (testing repo & account).

- **Phase 4 T2 deltas (default OFF):**
  - `tests/test_poller.py` (new, 12 tests) — pure helpers (`_normalize_ci_state`, `_review_decision_from_reviews`), tick behavior (noop when flag off, build facts, skip non-`jalebi/` branches, malformed head branch, httpx error, 401 drops repo state, ETag reuse + 304 keeps cached payload, stale-PR prune, flag-off prune, ETag cache cap), and thread lifecycle (start/stop clean, double-start no-op).
  - `tests/test_attention.py` — extended with 10 `attention_for` decision-tree tests (one per branch).
  - `tests/test_api_tasks.py` — `attention` field on task dicts (default-off path; via poller `record_facts`; without `JALEBI_POLLER`).
  - `tests/test_api_repos.py` — new `test_toggle_poll_fallback` (boolean validation, 404, empty payload).
  - `tests/test_github.py` — 5 new tests for `list_open_prs`, `list_check_runs_for_ref`, `list_reviews_for_pr` (with ETag + If-None-Match + 401 → `GitHubUnauthorized`).
- **Suite delta:** 682 → 720 (+38). No existing tests broken.
