# Jalebi Phase 0 — Comprehensive Code Review

> **Scope:** Full read-through of the Phase 0 implementation (server, adapters, routes, migrations, frontend), PRD cross-check, git-history secret audit, and live data-dir inspection.
> **Date:** 2026-08-06
> **Status:** Review complete; fixes planned (see "Approved fix plan" below).

---

## 1. Verification baseline

All claims below were independently verified, not assumed:

| Check | Result |
|---|---|
| `uv run pytest` (server) | **206 passed** |
| `npm test -w @jalebi/web` | **16 passed** |
| `uv run ruff check` (server) | clean |
| `npm run typecheck` / `npm run lint` (web) | clean |
| `npm run build` (web) | succeeds |
| Real `JALEBI_GITHUB_TOKEN` value in git history | **absent** (test files only use `ghp_secret`/`ghp_test` placeholders) |
| `.env` tracked in git | **no** — git-ignored (`git check-ignore` confirms) |
| `~/.jalebi/secrets.json` permissions | `0600` |
| Token in argv/URLs/clone URLs | **no** — token travels via `Authorization` headers and `GIT_CONFIG_*` env only |

**Strengths confirmed:**

- Masking is applied at the **ingest layer** (before any SSE broadcast / DB persist) and covers the primary PAT, all named PATs, and user-supplied regex `secret_patterns` across steps, console, prompts, follow-ups, and PR title/body/review text.
- The `gh` ban is genuine defense-in-depth: per-worktree `opencode.json` permission rules + env stripping of `GH_TOKEN`/`GITHUB_TOKEN`/`GH_CONFIG_DIR` + token-authenticated git creds. Live-proven against a real worktree.
- Publish is idempotent: PR lookup by head ref (`find_pr_by_head`), reuse of an existing PR, `Closes #N`, no duplicate PRs.
- Per-account token resolution on publish/follow-up/reconnect (no accidental use of the primary).
- Artifact path-traversal protection, soft repo disconnect, crash recovery (`recover()`), stall guard, type-validated settings, unknown `/api/*` → JSON 404.

---

## 2. Findings by severity

### 🔴 HIGH

**H1 — Create-task form breaks for repos owned by a named account** (`apps/web/src/pages/Tasks.tsx:124`)
The UI fetches issues/PRs/branches with `api.getGithubContext(repo.full_name)` — no `account` param — while the backend (`routes/github.py:156-181`) resolves the token from `?account=` and otherwise falls back to the **default** token. For a repo connected under a named account whose default token lacks access, the fetch 502s, `.catch(() => {})` swallows it, and the issue/PR/branch pickers stay stuck on "loading…" — you cannot create an `issue_fix`/`pr_review` task for that repo. The API supports `?account=`; the UI never sends it. (The backend `_fetch_context` on task-create is correct — it uses the repo's account — so this is a pre-fetch bug only.)

**H2 — Replacing the primary token via the UI is a silent no-op while `.env` is set** (`secrets.py:116-125`, `routes/github.py:86-103`, `app.py:50`)
`load_github_token()` returns the env var **first**, and `create_app` re-syncs the env value into the store at every startup. So `PUT /api/github/token` validates, stores, and reports `stored: true`, but the app keeps using the old env token and the stored token is overwritten on the next restart. The "Connect your default GitHub account" form can never take effect in a `.env`-based setup.

### 🔴 Security consideration (online-leak relevant)

**H3 — Token exfiltration via prompt injection** (`prompts.py:94-101`, `queue.py:37-57`)
The PAT is placed in the agent's environment, and **attacker-controlled content** (issue bodies / PR descriptions on public repos) is embedded verbatim into the worktree `AGENTS.md`. Masking only prevents *display* — it cannot stop a socially-engineered agent from `curl`-ing `$JALEBI_GITHUB_TOKEN` somewhere if a malicious issue instructs it to. Inherent to the product (F1/F6/F7 require both), but for public repos this is the one plausible way a secret leaves the machine.

### 🟠 MEDIUM

**M1 — Follow-up model override is recorded but never applied** (`queue.py:616,622-648`; `adapters/opencode.py:84-95`)
`_run_followup` computes `effective_model`, stores it on the run row, and records it on the follow-up — but `adapter.resume(...)` never receives a model, and `OpenCodeAdapter.resume` never builds `--model`. The follow-up **Model** dropdown (TaskDetail) therefore does nothing; opencode keeps the session's original model.

**M2 — Bootstrap files can be committed into task branches/PRs** (`worktree_bootstrap.py:79-89,125-135`)
The pre-commit hook and worktree `.gitignore` only cover `.jalebi/`. `AGENTS.md` and `opencode.json` are untracked and not ignored — verified live: `git status` in `ws/task-24` shows `?? AGENTS.md` / `?? opencode.json`. Worse: on repos that **track** a root `AGENTS.md`, `bootstrap_worktree` **clobbers** it, and a `git add . && git commit` by the agent pushes Jalebi task context into the PR. Contradicts the Step-24 goal of keeping Jalebi-internal files out of PRs.

**M3 — Deleting an account orphans disconnected repos + their tasks** (`routes/github.py:248-253`)
The cascade selects only `connected=True` repos. A disconnected repo bound to the account (and any task on it, including tasks explicitly using that account) survives, and its tasks then **silently fall back to the primary token** (`resolve_token`) after the account is gone — a misconfiguration with no warning. The `repos_affected`/`tasks_affected` counts also undercount.

**M4 — Cancel race can be lost for just-picked queued tasks** (`queue.py:366-405`, `routes/tasks.py:226-230`)
The worker reads `task.status` from its own session snapshot (still `queued`) before registering in `_running`. If the user cancels in that window, the route's *queued* branch sets `cancelled` in the DB but never calls `queue.cancel`; the worker then flips the task to `running` and runs it to completion despite the user seeing "cancelled".

### 🟡 LOW

- **L1 — SSE subscribe/close race** (`routes/tasks.py:372-375`): a page connecting exactly when a run finishes subscribes after `events.close()` popped everyone, so it never receives `stream_end` and can sit on "running" with keepalives until a manual reload.
- **L2 — Kill only reaches the direct child** (`queue.py:69-81`): `_kill_proc` terminates the opencode process, not its process group; grandchildren (MCP servers, git, model subprocesses) can be orphaned on cancel/timeout.
- **L3 — Stall guard 120s** (`queue.py:31`): kills any agent silent for 120s — legitimate long model/API turns are terminated as "stalled".
- **L4 — Hard-coded default model in the UI** (`Tasks.tsx:46`): `opencode-go/deepseek-v4-flash` is a hard default — deviation from PRD F5 ("no hard-coded model; default = adapter's configured default").
- **L5 — Localhost task URL posted to GitHub** (`queue.py:849-852,887-890`): every PR body and issue comment contained `http://127.0.0.1:3456/tasks/{id}` and a `Co-authored-by: Jalebi <jalebi@localhost>` footer. **RESOLVED** — templates moved to `apps/server/src/jalebi/messaging.py`; the new footer is `🦦 Opened by [Jalebi](https://github.com/samosa-ai-com/jalebi) — your self-hosted AI coding agent by [Samosa AI](https://github.com/samosa-ai-com).` and `Co-authored-by: Jalebi <jalebi@samosa-ai.com>`. Issue comments and PR reviews now link to the public Jalebi repo, not `127.0.0.1`. See `docs/14-messaging-strategy.md`.
- **L6 — Composer visibility mismatch** (`TaskDetail.tsx:527` vs `tasks.py:74-80`): the UI shows the follow-up composer only when the *latest* run has a `session_id`; the backend resumes the *latest resumable* run. If the latest run has no session but an older one does, the composer is hidden even though a follow-up is possible.
- **L7 — Misc**: UI concurrency caps at 16 while the API allows 64 (`Settings.tsx:87` vs `app.py:24`); `config.py:46-48` does a bare `int()` on `JALEBI_PORT` (crashes on junk) and the sqlite URL is not escaped for special chars in `JALEBI_DATA_DIR`; `recover()` return count omits interrupted *tasks* without a running run row (`queue.py:236`); manual `rerun` increments `retry_count`, which consumes the auto-retry budget.

---

## 3. PRD deviations observed

- Task status set adds `cancelled` (documented in HANDOFF as intentional — abort is terminal, PRD §F3).
- `auto_publish` is global-only; PRD F9 says "per-task or global". Documented as a known limitation in `docs/11`.
- UI hard-codes a default model (L4) — violates PRD F5's "no hard-coded model".

---

## 4. Approved fix plan (decisions captured from owner)

1. **H2** — Token source of truth = **stored (UI)**; `.env` is a test-only fallback. Remove env-first precedence and `persist_env_github_token`.
2. **H3** — Scrub/annotate `AGENTS.md` context: frame issue/PR bodies as UNTRUSTED DATA with delimiters + anti-exfiltration hard rules.
3. **M1** — Pass `--model` on resume (opencode supports `--model` with `--session` per PRD F4).
4. **M2** — Move bootstrap-file protection to `.git/info/exclude`; preserve-and-append instead of clobbering a tracked root `AGENTS.md`; restore bootstrap files to HEAD before `push_branch`.
5. **M3** — Account deletion cascades to disconnected repos (drop the `connected=True` filter).
6. **M4** — Register `_RunState` first in the worker; queued-cancel also calls `queue.cancel`.
7. **L1–L7** — SSE re-check after subscribe; `start_new_session` + `killpg`; stall guard 300s; remove hard-coded model; composer condition `runs.some(r => r.session_id)`; concurrency max 64; config port/db_url robustness; `recover()` count.
8. **L5** — ✅ **RESOLVED** — replaced PRD-mandated footer with the new template (`docs/14-messaging-strategy.md`); the `127.0.0.1` URL and `task <id>` are gone from external posts; `Co-authored-by` upgraded to `jalebi@samosa-ai.com`.

Files to touch: `secrets.py`, `app.py`, `routes/github.py`, `routes/tasks.py`, `queue.py`, `adapters/types.py`, `adapters/opencode.py`, `prompts.py`, `worktree_bootstrap.py`, `config.py`, `Tasks.tsx`, `TaskDetail.tsx`, `Settings.tsx`, plus tests and docs (`03/04/05/06/08/10/11/12`, `AGENTS.md` §3.1, `.env.example`, `HANDOFF.md`).

Verification: `uv run pytest`, `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`, `uv run ruff check`, then a live restart + smoke of the stored-token flow.
