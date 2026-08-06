# 05 — GitHub Integration

> **Scope:** GitHub client, PAT scopes + validation, webhooks, and check runs. Update this file for any GitHub client/webhook/check-run work.

---

## 1. The only credential

- **`JALEBI_GITHUB_TOKEN`** — the owner's GitHub personal access token, stored locally in the git-ignored `.env` (dev) or `<data-dir>/secrets.json` with `0600` permissions (runtime).
- **Python client (httpx):** `jalebi/github.py` — a thin REST wrapper (no PyGithub). The only component that talks to GitHub.
- **Secrets flow (`jalebi/secrets.py`):** at app startup, if `JALEBI_GITHUB_TOKEN` is set in the environment it is mirrored into `secrets.json` (`0600`, written atomically via a temp file). At runtime, `load_github_token()` prefers the env var, else the stored file. The `PUT /api/github/token` endpoint stores a token set from the Settings UI.
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

Planned capabilities (later phases): issues, PRs (create/update/comment/review), refs, webhook registration/management, commit statuses/check runs — all via the same client.

## 4. Webhooks (PRD §F14)

- Jalebi registers repo webhooks via the API targeting its local listener (URL + optional secret for signature verification).
- For a localhost-only install, GitHub cannot reach the machine — the listener must be exposed via a **tunnel (e.g. `cloudflared`/`ngrok`)** or the webhook URL points at a small reverse proxy.
- The app detects an unreachable webhook via a diagnostic status endpoint (`GET /api/webhook/status`) and warns in the UI, offering the **polling fallback**.
- **Idempotency:** deliveries are deduped on `X-GitHub-Delivery` / `X-GitHub-Event` headers, so re-deliveries never double-run a task.
- **Replay:** the UI offers "replay last delivery" for any event.

### Webhook listener flow

1. GitHub delivers a repo webhook event to the local listener (`POST /webhook`).
2. Listener validates (optional `X-Hub-Signature-256` secret) and **idempotently** dedups the delivery.
3. Matches the event against the user's **trigger rules**.
4. A matching rule creates and enqueues task(s) immediately (e.g. PR opened ⇒ assigned reviewers auto-start).
5. Runs proceed like manual tasks and report back via **check runs** on the PR head commit when configured.

## 5. Check runs & merge gating (PRD §F15)

- Jalebi creates **check runs** (commit statuses) on the head SHA of the branch it's working on, via the PAT (`POST /repos/{owner}/{repo}/check-runs`).
- Lifecycle mirrors a run: `queued` → `in_progress` (friendly name like `Jalebi / review (security-auditor)`) → `completed` with `conclusion` (`success`/`failure`/`neutral`/`cancelled`). Status updates are posted against the latest pushed HEAD SHA on `jalebi/<taskId>`.
- Because these are real check runs, **branch protection** can require them — merging is blocked until the agent's review/fix check is green. Opt-in per repo.
- Failure/success of the underlying task drives the conclusion; a follow-up updates the existing check rather than creating duplicates (matched by name + head SHA).

## 6. Reviewer posting (PRD §F7)

- On completion, the orchestrator posts the reviewer's output as a **PR review comment** on GitHub (e.g. `POST /repos/{owner}/{repo}/pulls/{n}/reviews` with `event: "COMMENT"` and body = the review).
- The UI tracks which reviewers have posted.
- **Approval is manual** — Jalebi never approves/merges.

## 7. Publish (PRD §F9)

- Push branch + open PR via Octokit: `base = target`, `head = jalebi/<taskId>`.
- Auto title = agent summary; body includes task instructions + `Closes #N` when an issue was referenced; footer with a link to the Jalebi task and `Co-authored-by` attribution for opencode.
- Follow-ups update the same PR (never a second PR for the same task).

## 8. Reference

- PRD §F1 (PAT), §F7 (reviewers), §F9 (publish), §F14 (webhooks), §F15 (check runs), §17.2 (no `gh` CLI).