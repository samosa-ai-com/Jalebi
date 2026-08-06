# 04 — Git Workspace

> **Scope:** Bare mirrors, worktrees, branch naming, and push with token. Update this file when the git workspace manager changes.

---

## 1. Overview

Jalebi uses the **git CLI** (not libgit2) for all repo operations. Each task/agent runs in an isolated **git worktree** created from a **bare mirror** of the repo. This keeps agents isolated from each other and from the user's working copy.

**Implementation:** `src/jalebi/git_workspace.py` — `GitWorkspace(config)` wrapping git via `subprocess`, with a per-repo `threading.Lock` around all shared-mirror mutations.

## 2. Directory layout (under data dir, default `~/.jalebi/`)

- `repos/<owner>__<repo>.git` — bare mirror of each repo.
- `ws/task-<taskId>/` — per-task worktree.

## 3. Bare mirror

- One bare mirror per repo, kept up to date by fetching refs.
- **`git clone --bare`** (not `--mirror`), normalized after clone: remote branches are tracked under `refs/remotes/origin/*` (`remote.origin.fetch = +refs/heads/*:refs/remotes/origin/*`, `remote.origin.mirror = false`), and a local `refs/heads/<default>` is kept in sync with `origin/<default>` purely so the mirror HEAD is valid (`git worktree add` requires HEAD under `refs/heads`).
- **Why not `--mirror`:** a mirror fetches `refs/*:refs/*` directly, which (a) refuses to fetch into a `jalebi/<taskId>` branch checked out in an active worktree, and (b) blocks pushes with an explicit refspec. With `origin/*` tracking, `fetch --prune` only touches remote-tracking refs — local task branches are safe, and plain `git push origin <branch>` works.
- `ensure_mirror(full_name, clone_url, token=None)` — clone + normalize, or `fetch origin --prune`, authenticated with the token (§6).
- **Concurrency lock:** a per-repo `threading.Lock` in `GitWorkspace` serializes all `clone`/`fetch`/`worktree` operations on the shared mirror, preventing concurrent workers from racing or producing `.git/config.lock` errors.

## 4. Worktree lifecycle

`create_worktree(task_id, full_name, base_branch="main", token=None)`, `remove_worktree(task_id, full_name)`, `push_branch(task_id, full_name, token)`, `commits_ahead(worktree, base_branch)`.

1. **Create / Resume:**
   - **New task:** `git worktree add -b jalebi/<taskId> <ws/task-<id>> origin/<source-branch>` — the worktree starts from the task's **source branch**.
   - **Resume / Follow-up:** if `jalebi/<taskId>` already exists in the mirror, `git worktree add <ws/task-<id>> jalebi/<taskId>` (without `-b`); if the worktree dir already exists it is reused as-is.
2. **Run:** the agent CLI is spawned with `cwd = <ws/task-<id>>` so it discovers `AGENTS.md`/skills.
3. **Discard:** `remove_worktree` runs `git worktree remove --force` and deletes the `jalebi/<taskId>` branch. `git worktree prune` on restart is a future cleanup step.

## 5. Branch naming

- Task branch: `jalebi/<taskId>`.
- PR: `head = jalebi/<taskId>`, `base = <target-branch>` (PRD §F8).

## 6. Push with token (never embed token in URL/logs)

- Auth is injected via the **`GIT_CONFIG_*` environment variables** so the PAT never appears in argv, URLs, or logs. GitHub requires **Basic** auth for git-over-HTTPS (Bearer works for the REST API but not for git), using `x-access-token:<PAT>`:
  ```
  GIT_CONFIG_COUNT=1
  GIT_CONFIG_KEY_0=http.extraHeader
  GIT_CONFIG_VALUE_0="Authorization: basic $(printf 'x-access-token:%s' "$JALEBI_GITHUB_TOKEN" | base64)"
  ```
- `push_branch` uses the worktree and `-c remote.origin.mirror=false` to push an explicit refspec (`jalebi/<taskId>`) against the `--mirror` clone.
- The token is the **only** credential (see `AGENTS.md` §3). The `gh` CLI is forbidden.

## 7. Source/target branch control (PRD §F8)

- **Source branch** — the base to branch off / the branch whose state the worktree starts from.
- **Target branch** — the PR base (`base`), where the fix will land.
- The orchestrator creates the worktree from **source**, opens the PR with `base = target`, `head = jalebi/<taskId>`.

## 8. Publish (PRD §F9)

- **Default: auto-publish** — on task completion, push the branch and open a PR (title = `[Jalebi] <first prompt line>`; body includes task instructions + `Closes #N` when an issue was referenced; footer links the Jalebi task and adds `Co-authored-by`).
- **Configurable:** global `auto_publish: true|false`; when `false` (or auto-publish fails), the UI shows a **"Publish"** button (push + open PR).
- **PR updates on follow-ups:** follow-ups amend the same branch; the existing PR is updated by the push (publish reuses the existing PR number) — never a second PR for the same task.

## 9. Cleanup / prune policy (PRD §F12)

- **Implemented:** artifact retention (`artifact_ttl_days`, startup prune). 
- **Planned (not implemented):** delete task worktrees for `done` tasks after a TTL (default 7 days) unless a PR is still open; `git worktree prune` on restart to recover orphaned worktrees.

## 10. Reference

- PRD §F8 (branch selection), §F9 (publish), §F12 (storage/prune), §17.2 (no `gh` CLI).
- Implementation: `src/jalebi/git_workspace.py` (+ tests in `tests/test_git_workspace.py`). Publish/PR creation is handled by the task-queue step (uses `push_branch` + the GitHub client).