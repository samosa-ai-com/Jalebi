# 05 — GitHub Integration

> **Scope:** GitHub client, PAT scopes + validation, webhooks, and check runs. Update this file for any GitHub client/webhook/check-run work.

---

## 1. The only credential

- **All PATs are equal named accounts.** Every token lives in the `0600` secrets file (`secrets.json` → `github_tokens: [{name, token}]`). There is **no primary/default account and no fallback**: the account selected for a task is the account used, and nothing silently substitutes a different one.
- **Python client (httpx):** `jalebi/github.py` — a thin REST wrapper (no PyGithub). The only component that talks to GitHub.
- **Secrets flow (`jalebi/secrets.py`):** `resolve_token(config, name)` returns the named account's token **or `None`** — never another account. `JALEBI_GITHUB_TOKEN` and a legacy stored `github_token` are used **only for masking** (so a stray value never survives into logs), never for resolution.
- **The `gh` CLI is forbidden** (PRD §17.2). No other token/credential is ever used (see `AGENTS.md` §3). No endpoint ever returns or logs the token.

## 2. Required scopes & validation (PRD §F1)

- **Classic PAT:** `repo`.
- **Fine-grained:** Contents read/write, Pull requests read/write, Issues read/write, Metadata read, **Commit statuses read/write** (for check runs).

`GitHubClient.validate_token()` calls `GET /user` and classifies the result:

- **Classic:** granted scopes are read from the `X-OAuth-Scopes` response header; `missing_scopes` lists exactly which required scopes are absent (`repo`).
- **Fine-grained:** GitHub exposes **no enumerable scope list**, so validation reports `valid=true` with a note to verify the per-resource permissions in the GitHub UI.
- Auth failure (401) → `valid=false` with GitHub's error message.

Validation is explicit (endpoints below), **not** run at startup — the server boots offline-friendly.

## 3. Client & endpoints (implemented)

`GitHubClient` (`jalebi/github.py`, httpx, `base_url=https://api.github.com`, `_request` seam for tests):

- `validate_token() -> TokenInfo` (`valid`, `login`, `token_type`, `granted_scopes`, `missing_scopes`, `note`, `error`).
- `get_repo(full_name)` → `{full_name, default_branch, clone_url, private}`; raises `GitHubNotFound` on 404.
- `create_pr(full_name, *, title, body, head, base) -> int` — opens a pull request and returns its number.
- `list_repos()` → `[{full_name, private, default_branch, clone_url, html_url}]`.

Blueprint `jalebi/routes/github.py` (`/api/github`):

| Endpoint | Behavior |
|---|---|
| `GET /api/github/status` | Validates the configured token; 409 if none configured; returns `TokenInfo`. |
| `GET /api/github/repos` | Lists the authenticated user's repos. 409 if no token. |

Account vault (`jalebi/routes/github.py`, `/api/github/tokens` — see §9 for details):

| Endpoint | Behavior |
|---|---|
| `GET /api/github/tokens` | Lists every named account with live status; never returns token values (only masked previews). |
| `POST /api/github/tokens` | Adds a new named account (validated first). **409 if the name already exists** — an existing account's credential is changed via `PUT`, never silently overwritten. |
| `PUT /api/github/tokens/<name>` | Replaces an existing account's PAT. Validates first (400 invalid / 502 unreachable / 404 unknown name); updates the token **and refreshes its metadata** without deleting any bound data — `repos.pat_name`/`tasks.pat_name` are name-keyed, so connected repos, tasks, and history survive. Returns `{updated, previous_login, login}` so the UI can flag an accidental identity change. |
| `DELETE /api/github/tokens/<name>` | Removes the account AND everything tied to it (repos, tasks, runs, worktrees, mirrors); queued/running tasks cancelled first. See §11. |

Connected-repo registry (`jalebi/routes/repos.py`, `/api/repos`):

| Endpoint | Behavior |
|---|---|
| `POST /api/repos` | `{"full_name": "<owner/repo>"}` → `GitHubClient.get_repo` → upsert into the `repos` table. 201 created / 200 updated; 409 no token; 404 not found; 400 bad body. |
| `GET /api/repos` | Lists connected repos from the `repos` table (DB, not GitHub). |

Implemented via the same client (Phase 0): issue/PR context fetch, publish (create/reuse PR), issue comments, PR review comments, paginated listing. **Phase 1:** webhook registration/management. **Phase 2:** commit statuses (see §5).

## 4. Webhooks (PRD §F14) — implemented (Phase 1)

- Jalebi registers repo webhooks via the API (`POST /api/repos/<id>/webhook`, using the repo's account — `github.create_hook`), targeting `<webhook_url>/webhook` with an optional HMAC secret. `DELETE /api/repos/<id>/webhook` unregisters. Registration is refused with a clear error when `webhook_url` is unset.
- For a localhost-only install, GitHub cannot reach the machine — the listener must be exposed via a **tunnel (e.g. `cloudflared`/`ngrok`)**; the owner sets the public base URL in Settings (webhook_url). The app detects an unreachable webhook (`GET /api/webhook/status`) and warns in the Triggers page UI.
- **Idempotency:** deliveries are deduped on `X-GitHub-Delivery`, so re-deliveries never double-run a task.
- **Replay:** the Triggers page offers "replay" for any logged delivery.
- **Auto-nudge (Phase 4 T4.2, default OFF):** every live delivery for a connected repo also passes through `nudger.on_webhook` — independent of trigger rules, so a failing `status` event on a `jalebi/<id>` branch enqueues a fix follow-up even with zero rules. Manual replays intentionally skip the nudge. Accepts both branch shapes (`"jalebi/12"` strings and real-GitHub `{"name": …}` objects).
- **Full flow, rule matching, and dispatch:** see `docs/16-triggers.md`.

### Webhook listener flow

1. GitHub delivers a repo webhook event to the local listener (`POST /webhook`).
2. Listener validates (optional `X-Hub-Signature-256` secret) and **idempotently** dedups the delivery.
3. Matches the event against the user's **trigger rules** (`/api/triggers`).
4. A matching rule creates and enqueues task(s) immediately (e.g. PR opened ⇒ assigned reviewers auto-start).
5. Runs proceed exactly like manual tasks (timeline, logs, diffs). **Commit statuses** are reported when the repo opts in (Phase 2 — see §5).

## 5. Commit statuses & merge gating (PRD §F15) — implemented (Phase 2)

**Why commit statuses, not check runs:** GitHub's *check-runs* API is GitHub-App only — PATs cannot write it, and Jalebi is PAT-driven (§F1). Merge gating uses **commit statuses** (`POST /repos/{owner}/{repo}/statuses/{sha}`), which the PAT *can* write ("Commit statuses read/write" is a required scope) and which **branch protection can require** — the same merge-gating outcome.

**What they're for (two purposes):**

1. **Merge gating (primary):** a status is a real, branch-protection-requireable check. Add the `Jalebi / fix` / `Jalebi / review` contexts to the repo's required status checks and GitHub physically blocks merging a PR until Jalebi's status is green.
2. **Informational signaling:** the status dot on the PR tells anyone looking at it — teammates, or another Jalebi instance — that Jalebi has **already** reviewed/fixed this commit, so they won't re-trigger a review. The `pending → success/failure/error` transition makes work-in-progress and outcome visible on the PR itself without opening Jalebi.

**Accuracy nuance:** the early "review in progress" (`pending`) dot is a **pr_review** behavior — Jalebi posts it on the PR head at run start. `issue_fix` posts its status only at **publish time** (the `jalebi/<id>` branch doesn't exist until the first push), so an issue_fix PR shows `pending` only briefly before flipping terminal.

- **Opt-in per repo:** the **Repos page** has a per-repo "check runs: on/off" toggle (`PATCH /api/repos/<id>` → `repos.check_runs_enabled`). Only `issue_fix` and `pr_review` tasks on such repos report statuses.
- **Status keying:** a commit status is keyed by `(sha, context)` — posting the same context again **replaces** GitHub's status for that SHA, which is the "update, don't duplicate" contract. Contexts: `Jalebi / fix` and `Jalebi / review`.
- **Lifecycle:** run start posts `pending`; run terminal posts the final state from `task.status` — `done → success`, `failed`/`timed_out → failure`, `cancelled`/`interrupted → error`, `needs_approval`/other → `pending` (still blocks a merge under branch protection).
- **Head SHA:** `pr_review` → the PR head SHA (available at run start). `issue_fix` → the pushed `jalebi/<taskId>` head, which exists only after the first push — so the status is set at **publish time** (auto or manual).
- **Registry:** the `check_runs` table mirrors each status (task, run, head SHA, context, state, GitHub status id). `tasks.check_run_id` points at the latest row (FK-less by design). A follow-up updates the existing status for the same head; a new pushed head gets a fresh one.
- **Non-fatal:** any status API failure is logged and never fails the underlying task.
- Because these are real commit statuses, **branch protection** can require them — merging is blocked until the agent's review/fix status is green. The owner enables the per-repo toggle and adds the status context to branch protection in the GitHub UI.

**GitHub client additions** (`jalebi/github.py`): `set_commit_status(full_name, sha, state, context, description=None) → status_id`. `get_pr` now also returns `head_sha`.

## 6. Reviewer posting (PRD §F7)

- **Assignment (Phase 1):** catalog agents of kind `reviewer` are assigned to a PR (via the task PR card or the new-task form's reviewer multi-select). Each reviewer runs as **its own `pr_review` task** (`reviews.assign_reviewers` creates one task per reviewer with the agent's pins + instructions and a `review_assignments` row linking task ↔ agent ↔ PR ↔ repo). They run in parallel under the queue's concurrency.
- **Execution:** the reviewer's task runs in a detached review worktree at the PR head (see `docs/04`), reviews with its own personality/skills/model/CLI, and on success the orchestrator posts its output as a **PR review comment** (`POST /repos/{owner}/{repo}/pulls/{n}/reviews`, `event: "COMMENT"`).
- The body is the agent's `.jalebi/review.md` (or the last assistant message as fallback), wrapped with a Jalebi header + CTA footer (`messaging.wrap_pr_review`). The body is masked for secrets **before** wrapping. The review event is `COMMENT` — Jalebi never approves/merges.
- **Status tracking:** the assignment transitions `queued → running → posted` (on review posted) or `failed` (run error). The PR card shows which reviewers have posted, with links to each reviewer task.
- **"Address the reviewers" follow-up (F7.6 / F11):** a follow-up with `include_reviews: true` fetches the PR's review comments (`github.list_pr_reviews`), masks them, embeds them into the prompt as UNTRUSTED DATA, and resumes the fixer (`prompts.build_address_reviewers_prompt`).
- **Approval is manual** — Jalebi never approves/merges.

## 7. Publish (PRD §F9)

- Push branch + open PR via the httpx GitHub client: `base = target`, `head = jalebi/<taskId>`.
- **PR title:** the agent's `.jalebi/pr.md` first `# <title>` line wins; on fallback, `messaging.pr_title(first_prompt_line)` produces `🦦 Jalebi: <line>` (or `🦦 Jalebi task` when the prompt is empty).
- **PR body:** the agent-written `.jalebi/pr.md` body (or the raw prompt as fallback), followed by the Jalebi footer from `messaging.pr_footer_body(closes=...)` — `Closes #N` (only for `issue_fix` with linked issues, only if not already in body), then the `🦦 Opened by [Jalebi](…) by [Samosa AI](…)` brand line, then `Co-authored-by: Jalebi <jalebi@samosa-ai.com>`. Never includes task IDs, `localhost`, or internal URLs — see `docs/14-messaging-strategy.md` for the full template + invariants.
- Follow-ups update the same PR (never a second PR for the same task).

## 8. Reference

- PRD §F1 (PAT), §F7 (reviewers), §F9 (publish), §F14 (webhooks), §F15 (check runs), §17.2 (no `gh` CLI).
## 9. Named PAT vault (multi-token)

- Jalebi stores **named PATs** in the `0600` secrets file (`secrets.json` → `github_tokens: [{name, token}]`). Every PAT is an equal account — there is no primary/default and no fallback.
- `GET/POST/PUT/DELETE /api/github/tokens` manage the vault. **Add** (`POST`) validates first (`validate_token`) and refuses an existing name (409); **update** (`PUT /api/github/tokens/<name>`) validates, then swaps the token and refreshes its metadata via `secrets.update_github_token` — name-keyed bindings (`repos.pat_name`, `tasks.pat_name`) are untouched, so replacing a credential **never deletes repos, tasks, or history**. The UI sees only **masked** previews (never values); the update response's `previous_login`/`login` let the UI warn when the new token authenticates as a different GitHub user.
- Tasks and follow-ups carry a `pat_name`; the queue resolves the token via `secrets.resolve_token(config, name)` (strict: named only, else `None` → an explicit error) and uses it for GitHub calls, the agent `JALEBI_GITHUB_TOKEN`, and masking. The agent env carries the selected PAT for GitHub **API** use but **no git push credentials** — Jalebi is the only pusher. **All** known PATs are masked at ingest.
- New client methods (httpx): `list_issues`, `get_issue`, `comment_on_issue`, `list_prs`, `get_pr`, `post_pr_review` (event `COMMENT`), `list_branches`, `find_pr_by_head` (same-repo dedup).
- `GET /api/github/context?repo=&account=` returns open issues + open PRs + branches for the task-form pickers.
- Fork metadata: `list_prs` returns `head_repo` / `head_sha` / `is_fork` per PR; `get_pr` additionally returns `head_clone_url` and `maintainer_can_modify` (whether the fork allows maintainer pushes — drives the fork `update_pr` vs `new_pr`-fallback decision). `is_fork` is true when the head repo differs from the base repo (or GitHub marks it a fork); a missing head repo (deleted fork) is treated as same-repo so callers fall back to the origin path with a clear error.

## 10. Publish dedup & PR accuracy

- `_publish` **recreates the worktree** from the mirror branch if it was cleaned (fixes "Publish on an old task did nothing").
- Auto-publish is gated by the task's **`publish_mode`** (`"auto"`/`"manual"`, defaulting by type: `issue_fix` → auto, freeform/manual types → manual; `None` falls back to the global `auto_publish` setting) — a freeform task only becomes a PR when the owner publishes it.
- Before `create_pr`, Jalebi calls `find_pr_by_head(full_name, "jalebi/<taskId>")` and **reuses** any existing **open** PR — so an agent-created PR can never produce a duplicate, and cross-repo fork PRs (e.g. the personal-account fork that caused task #14's duplicate) are ignored. `find_pr_by_head` queries `state=open` only and also checks each result's `state`; a **closed/merged PR** reusing the `jalebi/<taskId>` branch name (e.g. across a wipe/reconfigure) is never reused — publish falls through and opens a fresh PR. The same guard applies to a task whose stored `pr_number` already points at a now-closed PR.
- PR title/body come from the agent-written `.jalebi/pr.md` (title + actual-implementation description), falling back to the prompt. `Closes #N` + Jalebi footer (see `docs/14-messaging-strategy.md`) + `Co-authored-by` are appended. `issue_fix` tasks get an **issue comment** linking the PR (generated by `messaging.issue_comment_for_pr`) — **only when the PR is newly created** (a reuse/follow-up push stays silent).
- GitHub PR review comments are posted with `event: "COMMENT"` only — Jalebi never approves or merges. Review bodies are wrapped with `messaging.wrap_pr_review` so every review carries a Jalebi header + CTA footer.

## 11. Multi-account model (each PAT = an account, all equal)

- Every saved PAT is a first-class **account** and all are equal — there is no "default"/"primary" account.
- `GET /api/github/tokens` → `{accounts:[...]}` with **live validation** per account (`login`, `token_type`, scopes, valid/error) — one `/user` call each on load.
- `GET /api/github/repos` lists repos **across all accounts**, each tagged `account: <name>` (`?account=` filters). A failing account contributes an `{account, error}` entry, not a page failure.
- `repos.pat_name` records which account owns a connected repo. **Connecting a repo requires an explicit account** (`pat_name`); `prune`/`branches`/`reconnect` resolve each repo's token strictly from its `pat_name`.
- Task creation **requires an account**: the user-selected one, else the repo's bound account — never a fallback to anything else. The Credentials dropdown overrides the repo's account.
- Removing an account **deletes its repos (connected AND soft-disconnected) and the tasks on them** (runs, follow-ups, artifacts, worktrees, mirrors). Queued/running tasks for the account are cancelled first. `DELETE /api/github/tokens/<name>` returns `{removed, repos_affected, tasks_affected}` so the UI can confirm.
- GitHub list endpoints (`list_repos`/`list_issues`/`list_prs`/`list_branches`) follow `Link: rel="next"` pagination (capped at 10 pages) — nothing silently drops past page 1.

---

## 12. Phase 4 T2.1 — PR/CI polling observer

When a connected repo has `poll_fallback=True`, a daemon thread (`jalebi.poller.Poller`, registered as `JALEBI_POLLER` in `create_app`, started in `main()`) ticks every **30 s** and writes normalized `PRFacts` to an in-memory map keyed by `(repo_id, pr_number)`.

- **Default OFF.** No repo has the flag set on connect. The owner flips it on per-repo via `PATCH /api/repos/<id> {"poll_fallback": true}`. The flag also gates the new toggle for the same route (`check_runs_enabled` and `poll_fallback` may be PATCHed independently or together).
- **Per-PR fan-out only when the head branch starts with `jalebi/`.** Any other head branch is ignored (the poller still records `last_checked_at` and stores the PR-list ETag, but does not fetch per-PR state).
- **ETag cache** — a `dict[url_path, etag]` (FIFO eviction at `ETAG_CACHE_CAP = 512`). Three client methods participate:
  - `list_open_prs(full_name, *, etag=None)` → `(prs, new_etag, not_modified)`.
  - `list_check_runs_for_ref(full_name, ref, *, etag=None)` → `(state_body, new_etag, not_modified)`. **Reads combined commit-status**, not check-runs — PATs cannot read check-runs (GitHub-App-only). Combined-status is the same source `merge gating` uses, and is PAT-compatible.
  - `list_reviews_for_pr(full_name, pr_number, *, etag=None)` → `(reviews, new_etag, not_modified)`. Unlike `list_pr_reviews` (the older F7.6 follow-up helper), this returns **all** records including empty-body `APPROVED` / `CHANGES_REQUESTED` so the poller's `_review_decision_from_reviews` aggregator sees the final states.
- **304 short-circuit** — when the PR list returns 304, the poller skips the per-PR fan-out AND skips the stale-PR prune (the list didn't change, so the cached facts are still authoritative).
- **PAT rotation (HTTP 401)** — the poller catches `GitHubUnauthorized`, drops that repo's facts + etags, and logs a warning. The next tick re-warms from scratch (the owner re-flips the flag if they want to keep polling).
- **Transient errors** (`httpx.HTTPError` / non-401 `GitHubError`) — caught at the tick level; the repo is skipped for this tick, facts + etags retained.
- **Stale-PR prune** — at the end of each successful tick, facts whose PR is no longer in the open-PR list (merged / closed) are dropped.
- **Per-repo prune** — at the end of every tick, repos that were NOT polled this time (disconnected, or `poll_fallback` flipped off) have their facts + etags cleared. Closes the "stale facts after OFF" risk.
- **Read API** — `routes/tasks._task_dict` calls `poller.pr_facts_for_task(task.repo_id, task.id)` and passes the result to `tasks.task_to_dict` which derives the `attention` field via `jalebi.attention.attention_for(task, run, pr_facts)`.
- **Auto-nudge (Phase 4 T4.2, default OFF)** — the poller is constructed with the task queue and calls `nudger.on_poller_fact_change` only on transitions *into* `failure` / `changes_requested` (first sighting counts; signature dedup backstops). Best-effort; never fails the tick.

### 12.1 `attention` (Phase 4 T2.2)

One-word status consumed by the UI (T3) and `tasks.task_to_dict`:

| Value | When |
|-------|------|
| `needs_you` | T0 waiting-for-input; or terminal with CI failure / `changes_requested`; or non-terminal with running CI failure. |
| `working` | Queued / running / waiting_review (and no running-PR CI failure). |
| `in_review` | Terminal with facts and no mergeable conflict. |
| `ready_to_merge` | Terminal with facts and `mergeable == True`. |
| `done` | Terminal `done` task with no PR facts (poller off, or not published yet). `cancelled` tasks and dismissed attention also evaluate to `done` — a user-cancelled/acknowledged task never demands attention (deliberate §16.3 deviation from the original plan). |

T0's `waiting_input` flag takes precedence over everything. The derivation is pure (no network call) and runs at read time; in-memory state survives only as long as the process.
