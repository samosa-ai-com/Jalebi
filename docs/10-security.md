# 10 — Security

> **Scope:** Localhost binding, secrets, masking, sandboxing, and threat notes. Update this file for any security-related changes.

---

## 1. Localhost binding (PRD §F13)

- Server binds to **127.0.0.1** only.
- Optional UI password (env `JALEBI_PASSWORD` or `OPENCODE_SERVER_PASSWORD` reuse).
- If the app is ever exposed (tunnel), require the UI password and document the risk.

## 2. Secrets (PRD §F1, §F13)

- PAT stored with `0600` permissions in `<data-dir>/secrets.json`.
- Never logged, never sent to the browser, never passed to agent prompts.
- **Git auth transport:** the PAT is passed to git via the `GIT_CONFIG_*` environment variables (`http.extraHeader: Authorization: basic base64(x-access-token:<PAT>)`) — it never appears in argv, URLs, or logs (GitHub requires Basic auth for git-over-HTTPS; Bearer works for the REST API only).
- The token is the **only** credential (see `AGENTS.md` §3). The `gh` CLI is forbidden for testing; it is authorized only for local git operations on the Jalebi repo itself.

## 3. Secret masking in logs (PRD §F17)

- The PAT (and any user-marked secret) is **automatically masked** in the live console and stored run logs: any occurrence of the secret string is redacted (e.g. `***`), so even if an agent echoes an env var or token, the console never shows it.
- Implemented at the **ingest layer** (before events are broadcast/persisted), not as a display-only filter.
- Optional user-supplied extra secret patterns (regex) to mask beyond the PAT.

## 4. Sandboxing (PRD §F13)

- Agents execute arbitrary shell code by design — each child process is scoped to its own worktree (cwd).
- A settings toggle can add a sandbox wrapper (e.g. `bwrap`/`firejail`) later.

## 5. Publishing safety (PRD §F9, §14)

- Publishing is idempotent; follow-ups only ever touch the task's own branch.
- Default auto-publish means tasks push to GitHub unattended. Keep `auto_publish` toggle prominent; consider a per-repo "require approval" override for sensitive repos.

## 6. Webhook security (PRD §F14)

- Listener validates deliveries with optional `X-Hub-Signature-256` secret.
- **Idempotent** — deliveries deduped on `X-GitHub-Delivery`, so re-deliveries never double-run a task.

## 7. Threat notes

- **Token leak:** if the token is suspected leaked (pasted into a committed file, chat log, etc.), tell the user immediately so it can be revoked. Token values only ever live in git-ignored files (`.env`, `~/.jalebi/secrets.json`).
- **Exposure via tunnel:** a tunnel exposes the localhost app; require the UI password and document the risk.
- **Agent code execution:** agents run arbitrary shell code by design; scoped to worktrees; optional sandbox wrapper later.

## 8. Reference

- PRD §F1 (PAT), §F13 (security & privacy), §F14 (webhooks), §F17 (secret masking), §17.2 (no `gh` CLI).
## 9. Banning the `gh` CLI (the agent cannot use it)

Layered defense — **all three must hold** for an agent run:

1. **opencode permission deny** — every worktree gets an `opencode.json` whose `permission.bash` denies `gh`/`gh *`/full-path variants (`worktree_bootstrap.OPENCODE_GUARD`). Project config deep-merges over the user's global config, so the deny wins.
2. **Env hygiene** — the agent env never has `GH_TOKEN`/`GITHUB_TOKEN` (inherited ones are stripped), `GH_CONFIG_DIR` points at a nonexistent dir, and the only token is `JALEBI_GITHUB_TOKEN` (used by git/curl). Even a guard bypass cannot authenticate `gh`.
3. **Instructions** — `AGENTS.md` + follow-up prompts say never to use `gh`/forks, and working git credentials remove any incentive.

Remaining risk (documented): a hypothetical full-path `/usr/bin/gh` call inside a compound command could reach a shell, but it cannot act as the owner (no credentials). Jalebi itself never calls `gh`.

## 10. Multi-PAT handling

- Named PATs live only in the `0600` `secrets.json`; the API and UI never return token values (only masked previews).
- All known PATs are added to the ingest masker, so any token echoed by an agent is redacted to `***` everywhere (console, timeline, PR body).
