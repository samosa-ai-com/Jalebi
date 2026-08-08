# 10 — Security

> **Scope:** Localhost binding, secrets, masking, sandboxing, and threat notes. Update this file for any security-related changes.

---

## 1. Localhost binding (PRD §F13)

- Server binds to **127.0.0.1** only.
- **Implemented:** optional UI password — when `JALEBI_PASSWORD` (or `OPENCODE_SERVER_PASSWORD`) is set, every route except `/api/health` requires **Basic auth** (`WWW-Authenticate: Basic`; the browser prompts once, then sends credentials on same-origin API/SSE calls). Off by default; intended for tunnel exposure.
- If the app is ever exposed (tunnel), require the UI password and document the risk.

## 2. Secrets (PRD §F1, §F13)

- PAT stored with `0600` permissions in `<data-dir>/secrets.json`.
- Never logged, never sent to the browser, never passed to agent prompts.
- **Git auth transport:** the PAT is passed to git via the `GIT_CONFIG_*` environment variables (`http.extraHeader: Authorization: basic base64(x-access-token:<PAT>)`) — it never appears in argv, URLs, or logs (GitHub requires Basic auth for git-over-HTTPS; Bearer works for the REST API only). Git subprocesses get a **hermetic env**: inherited `GIT_CONFIG_*`/`GIT_DIR` state is stripped and `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` pinned, so a parent-shell credential helper or `url.insteadOf` cannot hijack Jalebi's git (and agent git commands get the same treatment).
- **Token precedence:** the **stored** token (`secrets.json`) is the source of truth (set via Settings); `JALEBI_GITHUB_TOKEN` is a test/bootstrap fallback and never overrides a stored token (PRD §F1).
- The token is the **only** credential (see `AGENTS.md` §3). The `gh` CLI is forbidden for testing; it is authorized only for local git operations on the Jalebi repo itself.

## 3. Secret masking in logs (PRD §F17)

- The PAT (and any user-marked secret) is **automatically masked** in the live console and stored run logs: any occurrence of the secret string is redacted (e.g. `***`), so even if an agent echoes an env var or token, the console never shows it.
- Implemented at the **ingest layer** (before events are broadcast/persisted), not as a display-only filter.
- Optional user-supplied extra secret patterns (regex) to mask beyond the PAT; patterns are **validated at submission** (an invalid regex is rejected with a 400 instead of silently ignored), and step text is **capped before masking** so a pathological pattern cannot backtrack over unbounded input.
- **Artifacts are masked too:** captured text files are run through the masker at write time; binary files that contain any known token value are dropped (not stored); a per-file 10 MB cap bounds a runaway agent. The run-end diff snapshot (`runs.diff_text`) is masked **before** truncation so a secret straddling the size boundary can't survive.

## 4. Sandboxing (PRD §F13)

- Agents execute arbitrary shell code by design — each child process is scoped to its own worktree (cwd).
- **Worktree-local confinement (config-level, no OS sandbox):** every worktree's `opencode.json` sets **`permission.external_directory: "deny"`** (deep-merged over the owner's global `external_directory: "allow"`). This blocks the built-in `read`/`edit`/`write`/`glob`/`grep` tools and path-bearing `bash` commands (e.g. `cat ~/.jalebi/secrets.json`) for anything outside the worktree, and `.env` files are denied by default. URL-based tools (`webfetch`/`websearch`, MCP URL tools) are unaffected, so the owner's MCP servers keep working.
- **Token-free freeform agents:** freeform/screen_finding runs get **no** `JALEBI_GITHUB_TOKEN` and no git `http.extraHeader` credentials (`_agent_token_for`) — they commit locally and Jalebi pushes. There is no credential to exfiltrate and nothing to push with. Only `issue_fix`/`pr_review` agents receive the PAT (for GitHub reads).
- **Honest limit (accepted tradeoff):** `external_directory: deny` is an opencode *pattern* gate, not an OS capability boundary. A determined agent obfuscating bash (`python3 -c "open(…)"`, env-var paths) could still reach host files. Combined with the token-free env there is little worth stealing; a true OS sandbox (e.g. `bwrap`/`firejail`) remains possible later but was intentionally **not** added (it would break the MCP servers the owner wants available).

## 5. Publishing safety (PRD §F9, §14)

- Publishing is idempotent; follow-ups only ever touch the task's own branch.
- **Per-task `publish_mode`:** `issue_fix` defaults to `"auto"`, freeform/manual types to `"manual"` — a freeform task only becomes a PR when the owner clicks Publish (or opts in to auto). `None` falls back to the global `auto_publish` setting.

## 6. Webhook security (PRD §F14)

- Listener validates deliveries with optional `X-Hub-Signature-256` secret.
- **Idempotent** — deliveries deduped on `X-GitHub-Delivery`, so re-deliveries never double-run a task.

## 7. Threat notes

- **Token leak:** if the token is suspected leaked (pasted into a committed file, chat log, etc.), tell the user immediately so it can be revoked. Token values only ever live in git-ignored files (`.env`, `~/.jalebi/secrets.json`).
- **Exposure via tunnel:** a tunnel exposes the localhost app; require the UI password and document the risk.
- **Agent code execution:** agents run arbitrary shell code by design; confined to the worktree via `external_directory: deny`; token-free for freeform. No OS-level sandbox (would break MCPs).

## 8. Reference

- PRD §F1 (PAT), §F13 (security & privacy), §F14 (webhooks), §F17 (secret masking), §17.2 (no `gh` CLI).
## 9. Banning the `gh` CLI (the agent cannot use it)

Layered defense — **all three must hold** for an agent run:

1. **opencode permission deny** — every worktree gets an `opencode.json` whose `permission.bash` denies `gh`/`gh *`/full-path variants (`worktree_bootstrap.OPENCODE_GUARD`). Project config deep-merges over the user's global config, so the deny wins.
2. **Env hygiene** — the agent env never has `GH_TOKEN`/`GITHUB_TOKEN` (inherited ones are stripped) or a leaked inherited `JALEBI_GITHUB_TOKEN`; `GH_CONFIG_DIR` points at a nonexistent dir. Only `issue_fix`/`pr_review` agents get `JALEBI_GITHUB_TOKEN` (used by curl for GitHub reads); freeform agents get **no** token and no git push credentials at all. Even a guard bypass cannot authenticate `gh`.
3. **Instructions** — `AGENTS.md` + follow-up prompts say never to use `gh`/forks, and working git credentials remove any incentive.

Remaining risk (documented): a hypothetical full-path `/usr/bin/gh` call inside a compound command could reach a shell, but it cannot act as the owner (no credentials). Jalebi itself never calls `gh`.

## 10. Multi-PAT handling

- Named PATs live only in the `0600` `secrets.json`; the API and UI never return token values (only masked previews).
- All known PATs are added to the ingest masker, so any token echoed by an agent is redacted to `***` everywhere (console, timeline, PR body).

## 11. Multi-account handling

- Each saved PAT is a separate account, but **all** PAT values live only in the `0600` `secrets.json`; API/UI never return raw values (masked previews only).
- All known PAT values are added to the ingest masker — any token echoed by an agent is redacted everywhere.
- The per-worktree gh-guard and env hygiene apply identically regardless of which account a task runs under.
