# 09 — Testing

> **Scope:** Test strategy per layer, how to run tests, and fixtures. Update this file for any test infrastructure changes.

---

## 1. How to run tests

- Server tests: `uv run pytest` (inside `apps/server`).
- Web tests: `npm test -w @jalebi/web` (vitest).
- Both: `npm test` (runs server pytest then web vitest).
- Fast server runs: `uv run pytest -n 4` (pytest-xdist, dev dep) — the suite is
  worker-safe (per-test tmp dirs + file DBs, no bound ports); ~14s for 931
  tests vs ~50s serial.
- Slowest-first triage: `uv run pytest --durations=12`.
- Lint/format: `uv run ruff check` (server); `npm run lint` / `npm run format` (web).
- Typecheck: `npm run typecheck` (web, `tsc --noEmit`).
- Build: `npm run build` (web → `apps/web/dist`, served by Flask).

After any code change, run the relevant tests and build before marking work done. Existing tests take precedence: if a change breaks a test, fix the code, not the test (unless the user explicitly asks to change the test).

## 2. Test layout

- **Server (`apps/server/tests/`, pytest):** `test_health`, `test_config`, `test_clock` (the app wall clock: local default, IANA override, offset-suffixed serialization), `test_schema` (migrations match models via `compare_metadata`), `test_settings` (defaults incl. bounded `retry_policy`, round-trip, cross-session persistence, **`seed_defaults` materializes all keys at startup without overwriting user values**, full-restart persistence), `test_api_settings` (incl. merged `ntfy_topic` validation, notify toggles, removed `ntfy_url`, `retry_policy` cap/pattern validation), `test_api_data` (usage, backup round-trip + snapshot consistency, vacuum, prune dry-run→execute + orphans), `test_reliability` (incl. **bounded recovery**: cap give-up with single notification, non-retryable fast-fail, always-failing task notifies twice then stops), `test_secrets` (strict no-fallback resolution), `test_github` (client, incl. fork-PR metadata: `head_repo`/`head_sha`/`is_fork`/`maintainer_can_modify`), `test_api_github` (equal-account model), `test_repos`, `test_api_repos` (connect requires an account), `test_git_workspace` (local git repos, incl. real `refs/pull/N` review worktree, PR-head fix worktree + fork-head merge, stale-branch reset, prune of missing-but-registered worktrees, and publish-time `merge_origin_into` clean + conflict-abort + no-merge-in-progress raise), `test_opencode_adapter` (incl. no server-env leak in `_spawn`), `test_codex_adapter` (parse mapping incl. the notices-not-fatal invariant, `turn.failed` JSON unwrap, sandbox-probe both branches, **sandbox fallback logs a one-time warning**, models-cache read incl. **`supported_in_api` preference**, spawn wrapper), `test_claude_adapter` (no-auth `is_error` discriminator, init session capture, `result` terminal mapping, spawn wrapper), `test_runhandle`, `test_masking`, `test_prompts` (incl. **catalog personality + `@path` skill links**), `test_address_reviews` (creation-time flag: bool/linked-PR/freeform validation, embedded vs self-fetch fallback prompt, legacy no-section default), `test_worktree_bootstrap` (incl. `external_directory: deny` guard, **per-CLI gh-guards — codex `.codex/rules/default.rules` (with `match`/`not_match` self-tests) + claude `.claude/settings.json` + the `PreToolUse` external-dir hook (`.claude/hooks/jalebi_deny_external.py`, unit-tested with hook JSON payloads), `CLAUDE.md` write for claude runs, all three guards skip repo-owned files (no-clobber) + remove_guard content-match cleanup, pre-commit hook rejects staged claude settings/skills, agent skills materialized to all three roots (`.claude`/`.codex`/`.agents`)/pruned when unused/excluded from git add**), `test_catalog` (service CRUD, slug/definition/skills validation, enable-only listing, file materialization), `test_api_catalog` (routes + task-creation with an agent), `test_reviews` (assign → one pr_review task per reviewer, kind/enabled validation, default prompt, status transitions, assignment dict, dedupe + partial-cleanup), `test_api_reviews` (assign endpoint, reviewers-on-create, kind validation, no-PR 409, address-reviewers follow-up embeds masked reviews, pr_number-column fallback), `test_webhooks` (signature verify, event-context extraction, rule CRUD + branch/author/label matching, delivery dedup, dispatch for start_review/triage_issue/create_task/rerun_review), `test_api_webhooks` (POST /webhook end-to-end incl. dedup + signature 403 + unconnected-repo ignore + **write-only webhook_secret + password-requires-secret + triage masking + concurrent redelivery race**, /api/triggers CRUD + validation, webhook status, replay-with-dedup, registration-without-URL 409, `/api/models?cli=` per-backend + unknown→empty + override), `test_artifacts`, `test_queue` (incl. selected-account agent env, dirty-tree surfacing, per-task publish mode, issue_fix single-target worktree base, publish conflict → needs_approval, issue comment on new-PR only, manual-publish no-op gate, **terminal + progress ntfy notifications, env-var injection into agent env, env-var value masking, catalog agent cli/model/custom-instructions/skills applied at run time + disabled-agent fallback, reviewer assignment lifecycle (posted on success, failed on posting error / non-done run)**), `test_cron` (5-field matcher: single/list/range/step values, invalid expressions, `datetime` wrapper + day-of-week shift), `test_screening` (findings parse incl. fenced/prose/bad input + string-literal bracket handling + severity normalization, run loop with fake adapter over a real git remote, baseline dedup + force, no-account failure, findings/output masking, error-event → failed, ntfy on findings when enabled / skipped when disabled, CRUD validation, scheduler `_due_screens` + `tick` with a fixed clock, unpinned screen resolves `default_backend` while a pinned one wins, default model applied only on the default backend), `test_api_screening` (templates, CRUD + validation, runs history, async run-now), `test_check_runs` (state mapping, status context, type+flag gating, pr_review pending→terminal on the PR head, issue_fix status set completed at publish, status API failure is non-fatal), `test_api_repos` (incl. `PATCH /api/repos/<id>` check_runs_enabled toggle + validation), `test_followups` (incl. follow-up backend override → fresh-session fork seeded with the prior conversation; same-backend resumes), `test_reliability`, `test_api_tasks` (incl. publish-mode defaults/override, oversized-prompt rejection, delete, account required, publish 409 on conflict/no-op, `env_vars` on create, PR-head sentinel validation + PR-base target resolution), `test_publish_modes` (incl. fork-PR `update_pr`: push to fork URL with head-SHA lease, maintainer-edits-off refusal, fork conflict propagation), `test_envvars` (service + routes: upsert/scope/`.env` import incl. empty/multiline/skipped-line reporting/masked API), `test_notify` (endpoint parsing, JSON publishing to server root, masked send, silent failure), `test_api_notify` (test endpoint), `test_sse`, `test_spa`. Fixtures in `conftest.py`: `config` (tmp data dir), `app` (via `create_app(config)`), `client` (Flask test client), `session`, `engine`. (~807 tests; `uv run pytest`.))
- **Web (`apps/web/src`, vitest + RTL):** `App.test.tsx` (shell), `pages/Tasks.test.tsx`, `pages/TaskDetail.test.tsx` (incl. run-end diff rendering with file label + stats, run history rows with status/duration/markers), `pages/Github.test.tsx`, `pages/Settings.test.tsx` (collapsible sections, blur-only IDE saves, custom IDE with zero detections, recovery-attempt save, stale-model warning, Data section), `pages/Repos.test.tsx` (incl. the check-runs toggle), `pages/Screenings.test.tsx` (incl. template-selection visibility, scope-branch dropdown, cron presets, edit-from-card, the in-flight "Running…" state, and the UNTRUSTED finding→task prompt),            `pages/Agents.test.tsx` (incl. the CLI override + model-pin dropdown options), `pages/Triggers.test.tsx` (rule form validation, agent picker, stale-form remount, delivery expansion + replay outcome, log filter). (144 tests; `npm test -w @jalebi/web`.)

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
- The `gh` CLI is authorized **only** for local git operations on the Jalebi repo itself (staging, committing, pushing to `samosa-ai-com/Jalebi`) — never for testing/verification against the testing account.

## 6. Runaway guardrails (no test may eat the machine)

- Every test is bounded by `pytest-timeout` (`timeout = 120`, signal method,
  configured in `apps/server/pyproject.toml`): a hanging test FAILS at 120s
  instead of looping forever. Serial full-suite wall time is ~60-90s, so 120s
  per test is generous — if the timeout ever fires, treat it as a hung-test
  bug, not a slow box.
- For unattended runs add a shell backstop too: `timeout 900 uv run pytest`.
- Prefer `uv run pytest -n auto` for speed; serial `uv run pytest` is the
  correctness baseline.
- If a run ever needs killing: kill only the `pytest`/`uv run pytest` PIDs.
  NEVER kill the daemon — its PID is in `~/.jalebi/server.pid` (port 2052).
  To find the hanging test, re-run with `-vv` and read the last test name
  before the stall (or the `Timeout` failure line).

## 7. Reference

- PRD §13 (non-functional requirements — testability), §17.2 (no `gh` CLI), §17.3 (testing repo & account).

- **Phase 4 T2 deltas (default OFF):**
  - `tests/test_poller.py` (new, 12 tests) — pure helpers (`_normalize_ci_state`, `_review_decision_from_reviews`), tick behavior (noop when flag off, build facts, skip non-`jalebi/` branches, malformed head branch, httpx error, 401 drops repo state, ETag reuse + 304 keeps cached payload, stale-PR prune, flag-off prune, ETag cache cap), and thread lifecycle (start/stop clean, double-start no-op).
  - `tests/test_attention.py` — extended with 10 `attention_for` decision-tree tests (one per branch).
  - `tests/test_api_tasks.py` — `attention` field on task dicts (default-off path; via poller `record_facts`; without `JALEBI_POLLER`).
  - `tests/test_api_repos.py` — new `test_toggle_poll_fallback` (boolean validation, 404, empty payload).
  - `tests/test_github.py` — 5 new tests for `list_open_prs`, `list_check_runs_for_ref`, `list_reviews_for_pr` (with ETag + If-None-Match + 401 → `GitHubUnauthorized`).
- **Suite delta:** 682 → 720 (+38). No existing tests broken.

- **Phase 4 T3 deltas (server +6, web +25):**
  - `tests/test_api_tasks.py` — +5 publish-check tests (`ready` / blocked branch / blocked nothing-to-publish / blocked conflict / attention CI failure) + 404 missing task.
  - `apps/web/src/components/AttentionBadge.test.tsx` (new, 7) — one render per attention value + pulsing/muted dot checks.
  - `apps/web/src/lib/unifiedDiff.test.ts` (new, 8) — added/removed/modified/renamed/binary/empty/malformed/backslash-no-newline cases.
  - `apps/web/src/pages/Tasks.test.tsx` — Needs-you filter chip + stat card + row attention dot (3 tests).
  - `apps/web/src/pages/TaskDetail.test.tsx` — merge-readiness panel renders per status (2 tests); existing diff test updated for the new viewer (no raw `diff --git` line; new viewer shows the file label + add/del stats).
- **Suite delta:** server 720 → 726 (+6); web 70 → 91 (+21). All gates green.

- **Phase 4 T4 deltas (server +37, web +21):**
  - `tests/test_dependencies.py` (new, 14) — add/self-ref/cycle, has_unmet_deps, dep_dict,
    cascade_unblock, delete_tasks_cascade drops dep edges both ways, route
    POST/DELETE 404/400 paths, rerun blocked → 409.
  - `tests/test_events.py` (new, 7) — persist, replay across restart, run_id filter,
    after_seq, prune cap, per-run scope.
  - `tests/test_nudger.py` (new, 5) — default off, skip running/done, dedup by
    signature, MAX_NUDGES_PER_TASK cap.
  - `apps/web/src/components/RunningCard.tsx` (new) + `apps/web/src/lib/runningCard.ts`
    (new) — covered via `npm run build` + manual smoke (the "live elapsed timer"
    is jsdom-friendly but is verified by smoke test, not by an automated unit).
- **Suite delta:** server 682 → 719 (T4 deltas); web 91 → 91 (T4.4 added a component
  covered by build only). All gates green.

- **Phase 4 T5 deltas (web +2):**
  - `Tasks.test.tsx` — wrapper has `max-h-[60vh]` + `overflow-y-auto`; thead has `sticky`.
  - `Github.test.tsx` — per-account repo list wrapper has `max-h-[26rem]` + `overflow-y-auto`; count line stays outside.
- **Suite delta:** web 91 → 93 (+2). All gates green (lint, typecheck, build).

- **Phase 4 T6 deltas (server +23, web +5):**
  - `tests/test_ide.py` (new, 23) — validator accepts/rejects, detect, resolve,
    test-open, spawn argv assertions (Popen mock), route-level 409/404/200,
    status/detect/test endpoints, settings validation round-trip.
  - `apps/web/src/pages/Settings.test.tsx` +3 — IDE section renders, Detect
    pre-fills + saves, Test open POSTs.
  - `apps/web/src/pages/TaskDetail.test.tsx` +2 — Open worktree posts
    `/open-in-ide` when configured; disabled + settings link when not.
- **Suite delta:** server 752 → 775 (+23); web 93 → 98 (+5). All gates green.

- **Phase 4 T7 deltas (server +11, web +7):**
  - `tests/test_workspace_files.py` (new, 11) — list root/subdir/empty,
    `.git` + `..` + symlink rejection, binary → 415, masked content,
    missing task / no worktree → 404.
  - `apps/web/src/components/FileBrowser.test.tsx` (new, 5) — entries,
    folder navigation, markdown viewer, mono pre, binary note.
  - `apps/web/src/pages/TaskDetail.test.tsx` +2 — FileBrowser mounts with
    a run; hidden without.
- **Suite delta:** server 775 → 786 (+11); web 98 → 105 (+7). All gates green.

- **Post-plan additions + pre-PR audit fixes (server +42 collected, web +14):**
  - Token rotation: `test_secrets.py` + `test_api_github.py` (update replaces
    token + meta, unknown name → 404/KeyError, invalid keeps old token, POST
    409 guard, login-change notice); `Github.test.tsx` (PUT submit, notices).
  - Fork-PR flow: `test_github` fork metadata, `test_api_tasks` sentinel
    validation + `_parse_number` 400s (garbage `pr_number`/`issue_number`),
    `test_publish_modes` fork push/refusal/conflict, `test_git_workspace`
    PR-head worktree + fork merge, `test_prompts` sentinel rendering,
    `Tasks.test.tsx` fork flow.
  - Attention: `test_attention.py` (cancelled → done, dismissed → done),
    `test_api_tasks.py` (cancel allows `needs_approval`, dismiss endpoint,
    `clear_attention_dismissal` helper, `_prepare_run` re-arm).
  - IDE expansion: `test_ide.py` (`detect_all_ides`, new detect payload);
    `Settings.test.tsx` (quick-pick cards, one-click save, test open);
    `TaskDetail.test.tsx` (header Open-in-IDE button).
  - Audit fixes: `test_workspace_files.py` (+3: intermediate symlink,
    escape-via-symlink-dir, `.Git`/`.GIT` variants); `test_nudger.py` (+3:
    object/string branch shapes, success ignored) + `test_poller.py` (+3:
    CI-failure and changes-requested transition wiring, queueless no-op) +
    `test_api_webhooks.py` (route-level status → nudge without rules);
    `Tasks.test.tsx` (+2 dep badges, +2 RunningCard contract incl. PR
    links); `TaskDetail.test.tsx` (+1 dep badges, +1 Files-above-Diff);
    `FileBrowser.test.tsx` (completion refresh on signal → 0);
    `test_events.py` (throttled prune sweep); `test_sse.py` (live
    no-duplicate replay, incl. fail-without-fix proof).
  - `test_check_runs.py::test_exception_path_closes_out_pending_status` is a
    known race-sensitive flake (passes isolated; fails ~rarely under full
    load) — see F10. Never `gh`-based; all git tests use local throwaway
    bare remotes, GitHub via mocks.
- **Suite totals at PR:** server **828 collected**, web **119 passed**.
  Full gates green (server suite modulo the known flake).

- **Settings overhaul + smart recovery + data management (server +10, web +5):**
  - `test_api_data.py` (new, 6) — usage, backup round-trip + snapshot
    consistency, vacuum, prune dry-run→execute + orphans.
  - `test_api_settings.py` (+1) — `retry_policy` cap/pattern validation.
  - `test_reliability.py` (+3) — cap give-up with single notification,
    non-retryable fast-fail, always-failing task notifies twice then stops.
  - `test_settings.py` — default `retry_policy` shape updated (bounded).
  - `Settings.test.tsx` (+5) — custom IDE with zero detections, blur-only IDE
    saves, recovery-attempt save, stale-model warning, Data section — plus
    collapsed-by-default, search narrowing, and the no-match empty note (+3).
- **Suite totals now:** server **853 passed**, web **128 passed**.
  Ruff, ESLint, `tsc --noEmit`, Prettier, production build clean (the Vite
  large-chunk warning remains).

- **AGY review fixes (server +5, web +3):**
  - `test_api_data.py` (+4) — prune task with a `CheckRun` row (no
    IntegrityError), deliveries scope removes screening worktrees, logs +
    orphan artifacts, backup `0600` + WAL counted in usage.
  - `test_reliability.py` (+1) — `notify_on_failed=false` suppresses the
    give-up push (still gives up silently with the timeline note).
  - `Settings.test.tsx` (+3) — failed save reverts the draft, expand-all
    opens every section, one-click stale-model fix saves the first model.
- **Suite totals now:** server **858 passed**, web **131 passed.**
  Ruff, ESLint, `tsc --noEmit`, Prettier, production build clean (the Vite
  large-chunk warning remains).

- **Settings round 2 — backends, timezones, presets, no-auto-delete (server +3, web +3):**
  - `test_api_settings.py` (+2) — `enabled_backends` validation + cross-field
    rules, `/api/backends` shape, `/api/timezones` shape.
  - `test_reliability.py` (+1) — `_enabled_cli` falls back to first enabled.
  - `test_events.py` — throttled-sweep test replaced with a no-auto-prune test
    (publishing never trims the table; `prune_task_events` kept as a utility).
  - `Settings.test.tsx` (+3) — backend uncheck saves the reduced list,
    timezone dropdown saves, secret preset tick saves the combined list.
- **Suite totals now:** server **861 passed**, web **134 passed.**
  Ruff, ESLint, `tsc --noEmit`, Prettier, production build clean (the Vite
  large-chunk warning remains).

- **Settings round 3 — restore UI, model copy (server +2, web +1):**
  - `test_api_data.py` (+2) — restore dry-run→execute round-trip (setting
    value comes back, safety snapshot listed), busy/unknown/confirm refusals.
  - `Settings.test.tsx` (+1) — restore previews then executes on RESTORE.
- **Suite totals now:** server **863 passed**, web **135 passed.**
  Ruff, ESLint, `tsc --noEmit`, Prettier, production build clean (the Vite
  large-chunk warning remains).

- **Triggers round — AGY review + Codex adjudication + full fix (server +13, web +9):**
  - `test_api_webhooks.py` (+13 net: 4 rewritten to the new contracts, 11 new) —
    bare `pull_request_review` firing, replay persist + error/empty retry +
    second-replay skip, PUT null-clears, rerun assignment reset, register
    adopt-on-422, unregister refuse/clear, rule validation, default-branch
    targeting, `triggered_by`, corrupt-result resilience, JSON 404 writes, 413.
  - `Triggers.test.tsx` (+8), `TaskDetail.test.tsx` (+1) — inline validation,
    per-action agent picker, stale-form remount, chips, secret warning,
    delivery expansion + task links, log filter, replay outcome, Started-by.
  - Adjudication record: Codex CONFIRMED the webhook `main` hardcode as a real
    bug (fixed: repo `default_branch` passed at dispatch); DOWNGRADED the
    missing triage/create agent picker (default-agent fallback, no failure)
    and the `poll_fallback` toggle (stays hidden — PRD-deferred; the running
    poller is the Phase 4 PR observer, not trigger fallback).
  - Intern-review follow-ups: dead-code + double-stamp cleanup, JSON 404 for
    all write methods (not just POST).
- **Suite totals now:** server **875 passed**, web **144 passed.**
  Ruff, ESLint, `tsc --noEmit`, Prettier, production build clean (the Vite
  large-chunk warning remains).

- **Screening round — Codex review (23 items) + full fix (server +13, web +8):**
  - `test_screening.py` (+7), `test_cron.py` (+1), `test_api_screening.py` (+5):
    strict findings parsing + garbage-fails-run, stale-run reconcile,
    delete-race recheck, nullable clears, model-drop on backend switch, finding
    caps, cron full-range OR, strict input types, preflight-failure runs with
    masking, SSE 404, summary lists.
  - AGY-review follow-ups (+4 server): 409-on-held-lock, no-ghost-row on
    runtime failure, strict rejection of non-finding arrays, scheduler-tick
    preflight persistence.
  - `Screenings.test.tsx` (+7), `App.test.tsx` (+1): loading state, card
    latest-run summary, prompt-review composer + task link, run-now endpoint +
    inline error, delete-disabled mid-run, model reset, branch/history error
    states, nav badge.
  - AGY-review frontend follow-ups (+2 web): badge clears on navigation,
    card refresh waits for the background run row.
- **Suite totals now:** server **892 passed**, web **154 passed.**
  Ruff, ESLint, `tsc --noEmit`, Prettier, production build clean (the Vite
  large-chunk warning remains).

- **Screening inbox round (server +3, web +3):**
  - `test_api_screening.py` (+3): inbox ordering + context, severity/screen
    filters + validation, empty case.
  - `Screenings.test.tsx` (+3): inbox list + severity filter, composer from
    inbox, health banner on failing screen.
- **Suite totals now:** server **895 passed**, web **157 passed.**
  Ruff, ESLint, `tsc --noEmit`, Prettier, production build clean (the Vite
  large-chunk warning remains).

- **Inbox AGY-review fixes (server +2, web +3):** unbounded scan bounded +
  fields coerced (incl. malformed-row test), `screen_id` garbage 400s,
  severity/screen filters server-side, unique inbox keys, sync-setState
  violations removed (real `npm run lint` green), text-filter + empty-inbox +
  screen-filter tests.
- **Suite totals now:** server **897 passed**, web **160 passed.**
  Ruff, `npm run lint`, `tsc --noEmit`, Prettier, production build clean (the
  Vite large-chunk warning remains).

- **Dealt-findings round (web +3):** hide-dealt-on-task-creation, reveal +
  reopen via toggle, Findings-default tab.
- **Suite totals now:** server **897 passed**, web **163 passed.**
  Ruff, `npm run lint`, `tsc --noEmit`, Prettier, production build clean (the
  Vite large-chunk warning remains).

- **Agents round (server +3, web +5):**
  - `test_api_catalog.py` (+3): strict create types, cli-switch drops model,
    usage endpoint (tasks + rules, 404).
  - `Agents.test.tsx` (+5): usage display + row toggle, stale-form remount,
    slug/skill inline validation, model reset on CLI switch, delete-impact
    confirm.
- **Suite totals now:** server **900 passed**, web **168 passed.**
  Ruff, `npm run lint`, `tsc --noEmit`, Prettier, production build clean (the
  Vite large-chunk warning remains; one pre-existing ruff I001 in
  `tests/test_api_catalog.py` untouched).

- **Skills/agents round (server +17, web +11):**
  - `test_catalog_skills.py` (+9): library CRUD/validation, link validation,
    resolve order/merge/unknown-skip, reference propagation, usage + delete
    guard, description/avatar validation.
  - `test_api_skills.py` (+4): skill routes, 409-with-agents, usage, agent
    link/meta + unknown-link 400s.
  - `test_seed_catalog.py` (+4): insert-noop, never-overwrites,
    deletion-stays-deleted, version-bump.
  - `Skills.test.tsx` (+8), `Agents.test.tsx` (+3): library page behaviors;
    avatar rows/suggestion/attach/override.
  - Migration `d4e5f6a7b8c9` verified end-to-end on a scratch DB (alembic to
    prior head → legacy inline rows → upgrade head: dedupe, `-2` collision
    suffix, links preserved, `skills_json` cleared).
- **Suite totals now:** server **917 passed**, web **179 passed.**
  `npm run lint`, `tsc --noEmit`, Prettier, production build clean (the Vite
  large-chunk warning remains).

- **Seed landing + review fixes (server +2):**
  - `test_seed_content.py` (+2): all 31 seed skills pass the service
    validator; all 16 agents link only seeded skills, valid avatars/kinds.
  - `conftest.py`: the `app` fixture wipes both catalog tables + resets the
    seed version per test (startup seeds would otherwise collide with test
    slugs; done in `app` — not autouse — so pure-tmp_path git tests never
    instantiate the app and its `data/` dir).
- **Suite totals now:** server **919 passed**, web **179 passed.**
  Ruff clean except one pre-existing I001 in `tests/test_api_catalog.py`
  (verified on the clean tree, untouched); `npm run lint`, `tsc --noEmit`,
  Prettier, production build clean.

- **Speed round (2026-09-07):** suite took 4–10+ min serial; now **~50s
  serial / ~14s with `-n 4`** (931 tests). Changes:
  - `conftest.py`: session-scoped `template_db` (migrated + seeded once),
    per-test file copy into the `app` fixture — replaces 20-migration +
    ~50-seed-insert bootstrap per test (~0.25s → ms). Catalog wipe kept, so
    each test still starts with an empty library.
  - `db.py`: `run_migrations` reuses a cached Alembic `ScriptDirectory`
    (revision files are pure code — safe across DBs in one process) with a
    plain-`upgrade` fallback on any surprise. Also speeds up production
    restarts marginally.
  - `screening.py`: watchdog stop flag → `threading.Event` (`wait(1)`);
    `join` returns immediately on run end instead of sleeping out a 1s
    quantum (~1s saved per screening-run test; no behavior change).
  - `test_sse.py`: fixed stale `_seq.get(task_id)` waits (key is
    `(task_id, run_id)` since durable-SSE) via a `_published_seq` helper —
    the backfill test alone burned ~20s spinning past two 10s deadlines.
  - `test_api_settings.py`: `test_models_endpoint_cli_query_param`
    monkeypatches `OpenCodeAdapter.list_models` (the real one spawns the
    `opencode models` CLI, ~1.5s; the test covers param routing, not the
    binary).
  - `pytest-xdist` added to dev deps (worker-safe: per-test tmp dirs +
    file DBs, no bound ports). Default stays serial; pass `-n auto` for
    speed. Remaining slowest tests are intentional waits (stall timeouts,
    worker-pool polling, concurrency threads).

- **Agents library-only round (web +5):** `Agents.test.tsx` 20 tests — slug
  validation, search/kind/status/sort, picker search, save-drops-inline,
  avatar suggestion/attach/override.
- **GitHub round (web +8):** `Github.test.tsx` 16 tests — repo search/filter/
  sort, error + retry, refresh, connect busy, scope guidance, collapse
  behaviors + persistence.
- **Suite totals now:** server **919 passed**, web **192 passed.**

- **Repos round (server +1, web +6 net):**
  - `test_api_repos.py` (+1): `?include_disconnected=1` reveals
    soft-disconnected repos (hidden by default).
  - `Repos.test.tsx` (rewritten, 1 → 7 tests): metadata rows, no connect
    form, search/account filter, statuses toggle, disconnected + reconnect,
    refresh, GitHub-page empty state.
- **Suite totals now:** server **920 passed**, web **198 passed.**

- **Task-63 lock-crash fix (server +3):**
  - `test_events_concurrency.py` (+3, new file): threaded publishers lose
    nothing; lock-failure isolation (caller session untouched); memory-only
    without run_id.
  - Independently validated by AGY (approve): caught a real showstopper
    (`db.get_session` factory unusable on worker threads → `db.Session`),
    snapshot-before-try for all three handlers, `_run_followup` repair
    verified clean. Pre-existing `test_api_catalog.py` I001 left untouched.
- **Suite totals now:** server **923 passed**, web **198 passed** (no frontend
  changes this round).

- **TaskDetail round (web +8):** `TaskDetail.test.tsx` 43 tests — queued
  polling + refresh, agent chip + vitality, single-run strip, timeline
  search/filter, follow-up hint, prompt + waiting-message copy, tab-visible
  resync (one pre-existing assertion widened: duplicate status pills).
- **Suite totals now:** server **923 passed**, web **206 passed.**
  No backend changes; daemon NOT restarted (owner constraint pending task 63).

- **Tasks queue overhaul (web +17):** `Tasks.test.tsx` 39 tests — benign
  attention hidden, cancelled pill, repo filter, clickable stat cards, row
  nav, prompt expand, clone prefill (+ branch preservation, proven against
  the unguarded version), running-card Cancel, row re-run, bulk delete +
  bulk dismiss, refresh stamp, collapsed Advanced, PR auto-branches,
  optional review prompt + default, creation flash. Three pre-existing tests
  adjusted for the collapsed Advanced section; `localStorage.clear()` per
  block (remembered task defaults leak across tests otherwise); clone scroll
  guarded for jsdom. AGY review (request-changes) caught 2 real blockers —
  clone-branch clobber by the context fetch (fixed with a consume-once
  preserve flag) and DepBadges links bubbling to the row nav — plus 5 nits
  (ID link keyboard access, offset-aware `timeAgo`, prompt-cell keyboard,
  clone pats/env, action coverage), all fixed.
- **Suite totals now:** server **923 passed**, web **223 passed.**
  No backend changes.

- **PR #5 review fixes (server +7, web +1):** triaged the Jalebi review —
  fixed M2/M3/M4/L1/L4/L6/L8 + C2, deliberately left M1 (product-level
  capacity call; per-(task,run) 2000-row cap already bounds a single run),
  L2 (bare-event semantics already in `docs/16-triggers.md`), L3 (replay
  idempotency semantics), L5 (safe fallback beats a stale pin), L7 (review
  premise wrong — `Run` has no `error` column; `steps_json` is the only
  output store), C1/C3/C4 (churn/no-action).
  - `test_api_data.py` (+4): restore 409 while a screening run is active
    (dry-run `busy_screenings`), vacuum lock → 409, preview counts
    legacy `run_id`-NULL events by `task_id`, `_chunked` unit test.
  - `test_api_screening.py` (+1): rare-severity inbox fills `limit` from
    deeper history (paged scan, 2000-run cap).
  - `test_catalog_skills.py` (+1): unknown skill link logs a warning.
  - `test_reliability.py` (+1): give-up note reports total attempts
    (initial + recoveries).
  - `Settings.test.tsx` (+1): restore button disabled + row shown while
    screening runs are busy.
- **Suite totals now:** server **930 passed**, web **224 passed.**
  Ruff `src/` clean (one pre-existing I001 in `tests/test_api_catalog.py`
  untouched); `tsc --noEmit`, production build clean (the Vite
  large-chunk warning remains).

- **Brew House round (web +12):** `components/brew/BrewHouse.test.tsx`
  (new, 12 tests) — kettle brews active task, idle slots, station click
  navigates, needs-you pulse, cold-kettles + paused-queue + empty-catalog
  states, skill rack glow, agent live counts, backends + default badge,
  live screens + findings ticker, worker-load ring. Classic Tasks view
  untouched (toggle defaults to queue; existing `Tasks.test.tsx` unmodified).
- **Suite totals now:** web **239 passed** (12 new; no backend changes).

- **Halwai Shop v2 (web +5 net):** `FryStation` (ex-`KettleStation`) and
  `MasalaDabba` (ex-`SpiceRack`) rewritten, `SweetShelf` + `snacks.ts` new;
  `BrewHouse.test.tsx` 17 tests — snack-per-type, strike-a-match + menu
  order callbacks, thali counts, served-ticker links, idle chatter,
  7-day shelf rendering. Shared `snackForType` moved to `snacks.ts`
  (react-refresh zero-warning rule). Classic view untouched.
- **Suite totals now:** web **244 passed** (no backend changes).

- **Halwai polish round (web +2):** chakli → pakora snack swap, centered
  kadhai strip, brushed-steel dabba + hammered thali + stacked-jalebi
  7-day shelf, "Today's menu" + "(spice box)" wording, personality chatter
  with hover-aware auto-scroll; `BrewHouse.test.tsx` 19 tests.
- **Suite totals now:** web **246 passed** (no backend changes).

- **Halwai v3 round (owner UX pass):** in-flow below-row chatter (Codex
  pick — no clipping, no auto-scroll needed), double-spiral jalebi glyph
  (AGY spec) at larger sizes, unexplained thali center motif removed,
  brushed-steel dabba rework (AGY spec: rim arcs, bowl glints, mound
  highlights), centered kadhai strip, pakora naming, "Today's menu" +
  "(spice box)", engineering menu board (glyph + count + real task type;
  order buttons pre-select the type via prefill remount), compacted
  single-viewport layout; `BrewHouse.test.tsx` 19 + `Tasks.test.tsx` +1
  (mission order prefill).
- **Suite totals now:** web **247 passed** (no backend changes).

- **Halwai revamp round (owner: cooks + ingredients + real stats):**
  `MasalaDabba`/`SweetShelf` deleted; new `ShopFloor` (karhai grid +
  order tickets + serving counter), `CooksRail`, `Pantry`, `StatsBoard`;
  `FryStation` gains per-stove status badges + cook/ingredient pins
  (enriched SR labels), `ControlShelf` gains compact rail mode; Settings
  sections deep-link (`?section=` force-open + scroll + flash, still
  collapsible; burner/backend/IDE links point at queue/agent/ide);
  AGY review (approve-with-nits) fixed before commit: deep-link
  lock-open, flash CSS moved global, bar-key collisions, unguarded
  `skill_ids`, ticker dup links hidden from AT, coil transition in
  reduced-motion; `BrewHouse.test.tsx` 24 (keyboard, sorting, spoiled),
  `Settings.test.tsx` +1 (deep-link + collapse), `TaskDetail.test.tsx`
  +1 (IDE href). Classic view untouched.
- **Suite totals now:** web **253 passed** (no backend changes).

- **Mission no-scroll round (owner: everything visible, no scrolling):**
  Today's menu moved out of the right rail into a single-row strip above
  the karhais (`ShopFloor` owns it via `menu` + `onNewTaskKind`);
  `StatsBoard` compressed to one 4-number row + short bars;
  `ControlShelf` compact renders 2×2 minis (smaller ring text, 2 live
  screens); `BrewHouse.test.tsx` +1 (menu-above-floor order),
  `Tasks.test.tsx` mission prefill updated to the chip name.
- **Suite totals now:** web **254 passed** (no backend changes).

- **Screenshot review round (throwaway 2062 instance, seeded demo data):
  fixed from real screenshots — stove SVG capped (`h-48`), 7-day bars
  actually render (`items-stretch` + `justify-end` columns; % heights
  need a definite parent), avg `whitespace-nowrap`, mini headers never
  wrap (compact links collapse to `→` with labels), menu chips share
  the strip (`flex-1` + truncate, tighter padding), rails narrowed to
  210/280 so all three chips fit. Verified 1600×900: no page scroll.

- **Owner-screenshot round (live 2-burner view, big void below floor):
  ** columns now fixed to `xl:h-[calc(100vh-16rem)]` so the floor fills
  the viewport; `FryStation` is `flex h-full flex-col` with the SVG at
  `min-h-40 flex-1` (karhais grow into the space); menu chips dropped
  the redundant `New →` (whole chip orders); rails 230/270 for cook
  names. Measured 227→871 of 900px, stoves 358px, docH ≈ vh.

- **Whitespace + badge-overflow round (owner live screenshot):**
  leftover space distributes between cards (ops grid `flex-1` +
  `content-between`; floor section `flex-1` with `auto-rows-fr` stove
  rows); `default` badge stacks under its backend in compact minis
  (was bursting the card → page h-scrollbar); minis `min-w-0`;
  columns sized `calc(100vh-260px)` to absorb main's pb-8 — measured
  docH == vh, zero scrollbars either axis at 1600×900.

- **Giant-stove round (stretch without a cap cropped the pots):
  ** stoves back to fixed sizes (`h-48`, `h-36` compact past 2 slots);
  floor section `justify-between` spreads leftover as gaps; grid keeps
  `min-h` + internal scroll fallback. Verified 1920×1080, 2 burners:
  docH == vh, no scrollbars.

- **Screening batch + New-task handoff (web +5 net, Codex REJECT → fixed):**
  `FindingTaskComposer` deleted; single/batch "New task" buttons navigate
  to `/` with location-state `ScreeningHandoff` (prefill + dealt
  fingerprints + id), consumed one-shot in `Tasks.tsx` (validate, nonce
  remount, replace-clear; `from` preserved) with dealt marked only in
  `handleCreated` after the POST succeeds. Tasks created as `freeform` +
  explicit `publishMode: "manual"` (documented semantic change from
  `screen_finding`; clone normalizes legacy `screen_finding`/`triggered`
  to `freeform`). Batch gate requires one repo + one effective target
  branch (`scope_branch || repo.default_branch`) with inline reasons;
  selection keyed `run:screen:items-index` via a render-scope map, cleared
  on refetch. Dealt helpers moved to `lib/screeningDealt.ts` (fingerprint
  semantics unchanged). Codex blockers fixed: nonce/re-inject (#1),
  dealt-at-navigation (#2, now dealt-on-success), freeform documentation
  (#3), effective-branch gate (#4). `Screenings.test.tsx` +3 net (handoff
  payload + no-POST, combined batch prompt, mixed-repo refusal, bulk
  dealt), `Tasks.test.tsx` +2 (handoff prefill + dealt-on-create,
  clone normalization).
- **Suite totals now:** server untouched, web **271 passed** (repo-qualified
  screen names: `qualifiedScreenName` in `lib/screeningPrompt.ts` + batch
  prompt headers; dropdown/rows/cards/banner/delete/tickers qualified;
  `Screenings.test.tsx` syncs + same-name-two-repos dropdown test,
  `BrewHouse.test.tsx` ticker assertion; no backend changes; production DB
  never touched; `tsc`, eslint, prettier, build clean — Vite large-chunk
  warning remains, pre-existing).

- **Open-findings rerun context (server +13, web +2):** `screening_dealt`
  table (migration `e7f8a9b0c1d2`, verified upgrade→downgrade→upgrade on a
  scratch DB, single head) + dealt endpoints + engine known-open context
  (repo-wide, current screen first, 20-item/400-char caps, fenced UNTRUSTED
  section, delimiter sanitization) + dealt-filtered ntfy. AGY
  APPROVE-WITH-CHANGES, all adopted except repo-wide scoping (owner chose
  repo-wide over same-screen-only; current-screen-first ordering is the
  compromise): canonical fingerprint column (SQLite NULL-UNIQUE trap),
  ntfy filter, delimiter escaping, async flash/rollback/import-flag,
  explicit catch on handoff mark, POST-reopen. `test_screening_dealt.py`
  (13: fingerprint parity, validation, idempotent mark/reopen, cascade,
  assembly incl. cross-screen dedupe + caps + omitted, fencing, prompt
  omission, end-to-end prompt capture, notify skip, full API roundtrip).
  Web: `lib/screeningDealt.ts` API-backed (legacy import-once + cache),
  dealt endpoints in `client.ts`, optimistic UI with rollback in inbox +
  history, handoff marks via API with visible error; tests for import,
  rollback, and handoff-mark-via-API.
- **Suite totals now:** server **944 passed**, web **274 passed**
  (273 + 1 handoff-mark-failure path). Production DB never touched; scratch DB only.
