# 04 — Git Workspace

> **Scope:** Bare mirrors, worktrees, branch naming, and push with token. Update this file when the git workspace manager changes.

---

## 1. Overview

Jalebi uses the **git CLI** (not libgit2) for all repo operations. Each task/agent runs in an isolated **git worktree** created from a **bare mirror** of the repo. This keeps agents isolated from each other and from the user's working copy.

## 2. Directory layout (under data dir, default `~/.jalebi/`)

- `repos/<owner>__<repo>.git` — bare mirror of each repo.
- `ws/<taskId>/` — per-task worktree.

## 3. Bare mirror

- One bare mirror per repo, kept up to date by fetching refs.
- Used as the source for creating worktrees and for ref-prefetch during screenings.
- Fetch with the token credential helper (see §6).

## 4. Worktree lifecycle

1. **Create:** `git worktree add <ws/<taskId>> -b jalebi/<taskId> <source-branch>` — the worktree starts from the task's **source branch**.
2. **Run:** the agent CLI is spawned with `cwd = <ws/<taskId>>` so it discovers `AGENTS.md`/skills.
3. **Discard:** on task completion/cleanup, `git worktree remove <ws/<taskId>>` (with `--force` if dirty), then `git worktree prune` to recover orphaned worktrees on restart.

## 5. Branch naming

- Task branch: `jalebi/<taskId>`.
- PR: `head = jalebi/<taskId>`, `base = <target-branch>` (PRD §F8).

## 6. Push with token (never embed token in URL/logs)

- Use a **credential helper** or `Authorization: Bearer $JALEBI_GITHUB_TOKEN` — never embed the token in a URL or command that gets logged.
- Example (credential helper approach):
  ```
  git -c http.extraheader="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$JALEBI_GITHUB_TOKEN" | base64)" push origin jalebi/<taskId>
  ```
  (or a one-shot credential helper that reads the token from the secrets file).
- The token is the **only** credential (see `AGENTS.md` §3). The `gh` CLI is forbidden.

## 7. Source/target branch control (PRD §F8)

- **Source branch** — the base to branch off / the branch whose state the worktree starts from.
- **Target branch** — the PR base (`base`), where the fix will land.
- The orchestrator creates the worktree from **source**, opens the PR with `base = target`, `head = jalebi/<taskId>`.

## 8. Publish (PRD §F9)

- **Default: auto-publish** — on task completion, push the branch and open a PR (auto title = agent summary; body includes task instructions + `Closes #N` when an issue was referenced; footer with a link to the Jalebi task and `Co-authored-by` attribution for opencode).
- **Configurable:** per-task or global `auto_publish: true|false`; when `false`, the UI shows a **"Publish"** button (push + open PR) and a "push-only" option.
- **PR updates on follow-ups:** follow-ups amend the same branch; existing PR is force-updated (new commit pushed) — never a second PR for the same task.

## 9. Cleanup / prune policy (PRD §F12)

- Delete task worktrees for `done` tasks after a configurable TTL (default 7 days) unless a PR is still open.
- On app restart, run `git worktree prune` to recover orphaned worktrees.

## 10. Reference

- PRD §F8 (branch selection), §F9 (publish), §F12 (storage/prune), §17.2 (no `gh` CLI).
- Recommended lightweight wrapper: `simple-git` (steveukx/simple-git) — but keep it simple; hand-rolled `child_process.exec` with careful arg handling is acceptable.