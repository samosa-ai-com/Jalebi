# 05 — GitHub Integration

> **Scope:** Octokit client, PAT scopes, webhooks, and check runs. Update this file for any GitHub client/webhook/check-run work.

---

## 1. The only credential

- **`JALEBI_GITHUB_TOKEN`** — the owner's GitHub personal access token, stored locally in the git-ignored `.env` (dev) or `<data-dir>/secrets.json` with `0600` permissions (runtime).
- All GitHub calls go through **Octokit** using this token.
- **The `gh` CLI is forbidden** (PRD §17.2). No other token/credential is ever used (see `AGENTS.md` §3).

## 2. Required scopes (PRD §F1)

- **Classic PAT:** `repo`.
- **Fine-grained:** Contents read/write, Pull requests read/write, Issues read/write, Metadata read, **Commit statuses read/write** (for check runs).

Validation at startup must confirm the token and enumerate granted scopes; the Settings UI shows exactly which scope is missing.

## 3. Capabilities used (via Octokit)

- Repo list & default branch.
- Issues (read, create).
- PRs (create/update/comment/review).
- Refs (fetch).
- Clone/push via authenticated git (see `docs/04-git-workspace.md`).
- **Repo webhook registration/management** (`POST /repos/{owner}/{repo}/hooks`).
- **Commit statuses / check runs** (`POST /repos/{owner}/{repo}/check-runs`).

## 4. Webhooks (PRD §F14)

- Jalebi registers repo webhooks via the API targeting its local listener (URL + optional secret for signature verification).
- For a localhost-only install, GitHub cannot reach the machine — the listener must be exposed via a **tunnel (e.g. `cloudflared`/`ngrok`)** or the webhook URL points at a small reverse proxy.
- The app detects an unreachable webhook and warns, offering the **polling fallback**.
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
- Lifecycle mirrors a run: `queued` → `in_progress` (friendly name like `Jalebi / review (security-auditor)`) → `completed` with `conclusion` (`success`/`failure`/`neutral`/`cancelled`).
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