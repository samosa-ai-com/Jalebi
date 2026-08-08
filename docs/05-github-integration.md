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
| `PUT /api/github/token` | Validates a submitted PAT; if valid, stores it in `secrets.json` (0600); never echoes it. 400 on invalid. |
| `GET /api/github/repos` | Lists the authenticated user's repos. 409 if no token. |

Connected-repo registry (`jalebi/routes/repos.py`, `/api/repos`):

| Endpoint | Behavior |
|---|---|
| `POST /api/repos` | `{"full_name": "<owner/repo>"}` → `GitHubClient.get_repo` → upsert into the `repos` table. 201 created / 200 updated; 409 no token; 404 not found; 400 bad body. |
| `GET /api/repos` | Lists connected repos from the `repos` table (DB, not GitHub). |

Implemented via the same client (Phase 0): issue/PR context fetch, publish (create/reuse PR), issue comments, PR review comments, paginated listing. **Planned (later phases):** webhook registration/management, commit statuses/check runs.

## 4. Webhooks (PRD §F14) — implemented (Phase 1)

- Jalebi registers repo webhooks via the API (`POST /api/repos/<id>/webhook`, using the repo's account — `github.create_hook`), targeting `<webhook_url>/webhook` with an optional HMAC secret. `DELETE /api/repos/<id>/webhook` unregisters. Registration is refused with a clear error when `webhook_url` is unset.
- For a localhost-only install, GitHub cannot reach the machine — the listener must be exposed via a **tunnel (e.g. `cloudflared`/`ngrok`)**; the owner sets the public base URL in Settings (webhook_url). The app detects an unreachable webhook (`GET /api/webhook/status`) and warns in the Triggers page UI.
- **Idempotency:** deliveries are deduped on `X-GitHub-Delivery`, so re-deliveries never double-run a task.
- **Replay:** the Triggers page offers "replay" for any logged delivery.
- **Full flow, rule matching, and dispatch:** see `docs/16-triggers.md`.

### Webhook listener flow

1. GitHub delivers a repo webhook event to the local listener (`POST /webhook`).
2. Listener validates (optional `X-Hub-Signature-256` secret) and **idempotently** dedups the delivery.
3. Matches the event against the user's **trigger rules** (`/api/triggers`).
4. A matching rule creates and enqueues task(s) immediately (e.g. PR opened ⇒ assigned reviewers auto-start).
5. Runs proceed exactly like manual tasks (timeline, logs, diffs). **Check runs** arrive in Phase 2.

## 5. Check runs & merge gating (PRD §F15) — Phase 2, NOT implemented

- **Planned:** Jalebi creates **check runs** (commit statuses) on the head SHA of the branch it's working on, via the PAT (`POST /repos/{owner}/{repo}/check-runs`).
- Lifecycle mirrors a run: `queued` → `in_progress` (friendly name like `Jalebi / review (security-auditor)`) → `completed` with `conclusion` (`success`/`failure`/`neutral`/`cancelled`). Status updates are posted against the latest pushed HEAD SHA on `jalebi/<taskId>`.
- Because these are real check runs, **branch protection** can require them — merging is blocked until the agent's review/fix check is green. Opt-in per repo.
- Failure/success of the underlying task drives the conclusion; a follow-up updates the existing check rather than creating duplicates (matched by name + head SHA).

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
- `GET/POST/DELETE /api/github/tokens` manage the vault; add validates first (`validate_token`), the UI sees only **masked** previews (never values).
- Tasks and follow-ups carry a `pat_name`; the queue resolves the token via `secrets.resolve_token(config, name)` (strict: named only, else `None` → an explicit error) and uses it for GitHub calls, the agent `JALEBI_GITHUB_TOKEN`, and masking. The agent env carries the selected PAT for GitHub **API** use but **no git push credentials** — Jalebi is the only pusher. **All** known PATs are masked at ingest.
- New client methods (httpx): `list_issues`, `get_issue`, `comment_on_issue`, `list_prs`, `get_pr`, `post_pr_review` (event `COMMENT`), `list_branches`, `find_pr_by_head` (same-repo dedup).
- `GET /api/github/context?repo=&account=` returns open issues + open PRs + branches for the task-form pickers.

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
