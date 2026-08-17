# 10 — Security

> **Scope:** Localhost binding, secrets, masking, sandboxing, and threat notes. Update this file for any security-related changes.

---

## 1. Network binding & auth (PRD §F13)

- Server binds to **0.0.0.0** by default for local network (LAN) access (configurable via `JALEBI_HOST=127.0.0.1` or any custom interface IP).
- **Implemented:** optional UI password — when `JALEBI_PASSWORD` (or `OPENCODE_SERVER_PASSWORD`) is set, every route except `/api/health` requires **Basic auth** (`WWW-Authenticate: Basic`; the browser prompts once, then sends credentials on same-origin API/SSE calls). Off by default; intended for LAN or tunnel exposure.
- If the app is exposed on a shared network or tunnel, set `JALEBI_PASSWORD` to protect access.
- **Failed-login notifications:** every wrong-password attempt pushes an ntfy alert ("failed login attempt" + client IP, attempted username masked so a real PAT used as a username never ships raw) to the configured `ntfy_topic`. Pushes are **throttled to one per client per 60s** (a brute-force scan can't flood the channel) and run on a **daemon thread** — a dead/slow ntfy server never blocks or delays the 401 response, only the alert is dropped.

## 2. Secrets (PRD §F1, §F13)

- PAT stored with `0600` permissions in `<data-dir>/secrets.json`.
- Never logged, never sent to the browser, never passed to agent prompts.
- **Git auth transport:** the PAT is passed to git via the `GIT_CONFIG_*` environment variables (`http.extraHeader: Authorization: basic base64(x-access-token:<PAT>)`) — it never appears in argv, URLs, or logs (GitHub requires Basic auth for git-over-HTTPS; Bearer works for the REST API only). Git subprocesses get a **hermetic env**: inherited `GIT_CONFIG_*`/`GIT_DIR` state is stripped and `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` pinned, so a parent-shell credential helper or `url.insteadOf` cannot hijack Jalebi's git (and agent git commands get the same treatment).
- **No primary token:** all PATs are equal named accounts (the vault). `JALEBI_GITHUB_TOKEN` / a legacy `github_token` are masking inputs only — never used to resolve which account runs a task (PRD §F1).
- The token is the **only** credential (see `AGENTS.md` §3). The `gh` CLI is forbidden for testing; it is authorized only for local git operations on the Jalebi repo itself.

## 3. Secret masking in logs (PRD §F17)

- The PAT (and any user-marked secret) is **automatically masked** in the live console and stored run logs: any occurrence of the secret string is redacted (e.g. `***`), so even if an agent echoes an env var or token, the console never shows it.
- Implemented at the **ingest layer** (before events are broadcast/persisted), not as a display-only filter.
- Optional user-supplied extra secret patterns (regex) to mask beyond the PAT; patterns are **validated at submission** (an invalid regex is rejected with a 400 instead of silently ignored), and step text is **capped before masking** so a pathological pattern cannot backtrack over unbounded input.
- **Artifacts are masked too:** captured text files are run through the masker at write time; binary files that contain any known token value are dropped (not stored); a per-file 10 MB cap bounds a runaway agent. The run-end diff snapshot (`runs.diff_text`) is masked **before** truncation so a secret straddling the size boundary can't survive.
- **Env vars are secrets:** the `env_vars` store's values are **never returned in full** by the API (masked previews only), and the selected values are added to the masker at run start so an agent that echoes them is redacted in steps/artifacts/diffs. They're injected into the agent env on top of the pinned env — they can never override `JALEBI_GITHUB_TOKEN`/git identity/gh hygiene.
- **Notifications are masked too:** ntfy pushes run the title/message through the masker before send, so a secret can't leak to the push channel.

## 4. Sandboxing (PRD §F13)

- Agents execute arbitrary shell code by design — each child process is scoped to its own worktree (cwd).
- **Worktree-local confinement is per-CLI** (`worktree_bootstrap.write_guard`):
  - **opencode:** every worktree's `opencode.json` sets **`permission.external_directory: "deny"`** (deep-merged over the owner's global `external_directory: "allow"`). This blocks the built-in `read`/`edit`/`write`/`glob`/`grep` tools and path-bearing `bash` commands (e.g. `cat ~/.jalebi/secrets.json`) for anything outside the worktree, and `.env` files are denied by default. URL-based tools (`webfetch`/`websearch`, MCP URL tools) are unaffected, so the owner's MCP servers keep working. Skipped (never clobbers) when the repo ships its own `opencode.json`.
  - **codex:** the adapter's sandbox (`-c sandbox_mode=workspace-write` or `danger-full-access`, probed per machine; the fallback logs a one-time warning with the host-level fix) is the confinement; the worktree's `.codex/rules/default.rules` denies `gh` (loaded for trusted projects; inline `match`/`not_match` self-tests verify the rule). Skipped when the repo ships its own `.codex/rules/default.rules`.
  - **claude:** `.claude/settings.json` denies `gh` and sensitive home paths (`~/.ssh`, `~/.aws`, `~/.config`, `~/.netrc`, `~/.git-credentials`, `~/.codex`, `~/.claude*` — applies in every permission mode, incl. `bypassPermissions`), plus a **`PreToolUse` hook** (`.claude/hooks/jalebi_deny_external.py`) that blocks the file tools (`Read`/`Write`/`Edit`/`Glob`/`Grep`) from touching anything outside the worktree — the claude equivalent of `external_directory: deny`. Skipped when the repo ships its own `.claude/settings.json`.
- **Selected-account agents:** every task runs as the account the owner picked; the agent's `JALEBI_GITHUB_TOKEN` is that account's PAT (GitHub API use only). The agent gets **no git push credentials** (`auth_env` is never applied to agent envs), so it structurally cannot push — Jalebi is the only pusher. There is no default/fallback: a task without an account is refused at creation.
- **Honest limit (accepted tradeoff):** these are *pattern/tool gates*, not OS capability boundaries. A determined agent obfuscating bash (`python3 -c "open(…)"`, env-var paths) could still reach host files — opencode's `external_directory: deny` and the claude `PreToolUse` hook both govern *recognized* tool calls and explicit path arguments, not arbitrary subprocess file I/O (e.g. `npm` touching `~/.npm`). The agent holds the selected account's PAT (API use), so a fully malicious agent could read the token from its own env; a true OS sandbox (e.g. `bwrap`/`firejail`) remains possible later but was intentionally **not** added (it would break the MCP servers the owner wants available).
- **Self-disable note (all three backends):** the guard files live *inside* the worktree and the running agent has full write access to them. The opencode agent can rewrite `opencode.json`; the codex agent can rewrite `.codex/rules/default.rules`; the claude agent can rewrite `.claude/settings.json` and `.claude/hooks/jalebi_deny_external.py`. The pattern gates are therefore advisory against a *benign-but-confused* agent, not a boundary against a malicious one — exactly the opencode accepted tradeoff, now stated explicitly for codex and claude. **The real backstop is the env hygiene** (`GH_TOKEN`/`GITHUB_TOKEN` stripped, `GH_CONFIG_DIR=/nonexistent-jalebi-gh`, no git push credentials, `JALEBI_GITHUB_TOKEN` only valid for the GitHub REST API) — a guard-bypassing agent still has no credentials to take.

## 5. Publishing safety (PRD §F9, §14)

- Publishing is idempotent; follow-ups only ever touch the task's own branch.
- **Per-task `publish_mode`:** `issue_fix` defaults to `"auto"`, freeform/manual types to `"manual"` — a freeform task only becomes a PR when the owner clicks Publish (or opts in to auto). `None` falls back to the global `auto_publish` setting.

## 6. Webhook security (PRD §F14)

- Listener validates deliveries with optional `X-Hub-Signature-256` secret.
- **Idempotent** — deliveries deduped on `X-GitHub-Delivery`, so re-deliveries never double-run a task.

## 7. Threat notes

- **Token leak:** if the token is suspected leaked (pasted into a committed file, chat log, etc.), tell the user immediately so it can be revoked. Token values only ever live in git-ignored files (`.env`, `~/.jalebi/secrets.json`).
- **Exposure via tunnel:** a tunnel exposes the localhost app; require the UI password and document the risk.
- **Agent code execution:** agents run arbitrary shell code by design; confined to the worktree via `external_directory: deny`. The selected account's PAT is exposed for GitHub API use but **no git push credentials** are given — the agent cannot push.

## 8. Reference

- PRD §F1 (PAT), §F13 (security & privacy), §F14 (webhooks), §F17 (secret masking), §17.2 (no `gh` CLI).
## 9. Banning the `gh` CLI (the agent cannot use it)

Layered defense — **all three must hold** for an agent run:

1. **Per-CLI permission deny** — every worktree gets a guard denying `gh`:
   - opencode → `opencode.json` `permission.bash` denies `gh`/`gh *`/full-path variants (`worktree_bootstrap.OPENCODE_GUARD`); project config deep-merges over the user's global config, so the deny wins.
   - codex → `.codex/rules/default.rules` Starlark `prefix_rule(pattern=[...])` lines for `gh`, `/usr/bin/gh`, `/usr/local/bin/gh`, `/opt/homebrew/bin/gh` (verified live with `codex execpolicy check`); the `prefix_rule` grammar only matches the command's first token, so multi-token wrapper coverage (`command gh`, `which gh`, `bash -c "gh ..."`) relies on env hygiene (no `GH_TOKEN`/`GITHUB_TOKEN`, `GH_CONFIG_DIR=/nonexistent-jalebi-gh`).
   - claude → `.claude/settings.json` `permissions.deny` covers `Bash(gh*)` variants + absolute paths (`/usr/bin/gh*`, `/usr/local/bin/gh*`, `/opt/homebrew/bin/gh*`) + wrappers (`command gh*`, `which gh*`, `type gh*`, `hash gh*`); deny rules apply in every permission mode (incl. `bypassPermissions`); skipped when the repo ships its own settings file.
2. **Env hygiene** — the agent env never has `GH_TOKEN`/`GITHUB_TOKEN` (inherited ones are stripped) or a leaked inherited `JALEBI_GITHUB_TOKEN` (set to `None`, and `_spawn` builds from the passed env so nothing from the server leaks in); `GH_CONFIG_DIR` points at a nonexistent dir. Every task's `JALEBI_GITHUB_TOKEN` is the **selected account's** PAT (GitHub API), and **no git push credentials** are given to any agent. Even a guard bypass cannot authenticate `gh`.
3. **Instructions** — `AGENTS.md` + follow-up prompts say never to use `gh`/forks, and working git credentials remove any incentive.

Remaining risk (documented): for codex specifically, multi-token wrapper calls (`command gh`, `which gh`) cannot be expressed in the available `prefix_rule` grammar; the env-hygiene layer is what blocks them. For opencode/claude the coverage is symmetric. Jalebi itself never calls `gh`.

## 10. Multi-PAT handling

- Named PATs live only in the `0600` `secrets.json`; the API and UI never return token values (only masked previews).
- All known PATs are added to the ingest masker, so any token echoed by an agent is redacted to `***` everywhere (console, timeline, PR body).

## 11. Multi-account handling

- Each saved PAT is a separate account, but **all** PAT values live only in the `0600` `secrets.json`; API/UI never return raw values (masked previews only).
- All known PAT values are added to the ingest masker — any token echoed by an agent is redacted everywhere.
- The per-worktree gh-guard and env hygiene apply identically regardless of which account a task runs under.

---

## 12. Phase 4 T1 security additions

- **Env-var block-list (`envvars.ENV_BLOCK_LIST` + `ENV_BLOCK_PREFIXES`, T1.1).** Closes the override hole in `_agent_env` where a stored env var could replace a Jalebi-pinned value (`GIT_CONFIG_KEY_0` re-injecting credential / `url.insteadOf` state, `JALEBI_GITHUB_TOKEN` overriding the queue's account selection, etc.). Block-all for the `GIT_CONFIG_*` prefix — there is no legitimate user surface. See `docs/14-env-vars.md` §8 for the full list and rationale.
- **Branch-mismatch guard before publish (T1.5).** `GitWorkspace.assert_publish_branch` refuses when the worktree HEAD is not on `jalebi/<taskId>`; an agent that checked out / detached onto another branch would otherwise push the wrong ref. Runs as the first line of `TaskQueue._publish` (covers both the manual `publish_task` route and the auto-publish path in `_stream_and_finish`).
- **Predictive conflict check via `git merge-tree --write-tree` on the mirror (T1.4).** Read-only (no worktree mutation, no abort dance). Branch is always the canonical `jalebi/<id>` — never client-supplied. Path-safety: paths come from the merge-tree file-info lines (stages 1/2/3), kinds from `CONFLICT (kind)` lines; fallback to `content` when no `CONFLICT` line is found. Requires git ≥ 2.38.
