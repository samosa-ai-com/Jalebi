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

`create_worktree(task_id, full_name, base_branch="main", token=None)`, `remove_worktree(task_id, full_name)`, `push_branch(task_id, full_name, token)`, `commits_ahead(worktree, base_branch)`, `merge_origin_into(worktree, full_name, base_branch, token)`.

1. **Create / Resume:**
   - **New task:** `git worktree add -b jalebi/<taskId> <ws/task-<id>> origin/<base-branch>` — the worktree starts from the task's **base branch**. `issue_fix` uses the **single-target model**: the base is the **target branch** (the PR base), so the PR diff is exactly the agent's fix and merges cleanly (PRD §F8's two-selector design was superseded). Other task types keep the source branch as the base.
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
- `push_branch` pushes the explicit refspec `jalebi/<taskId>` from the worktree, under the per-repo mirror lock.
- The token is the **only** credential (see `AGENTS.md` §3). The `gh` CLI is forbidden.

## 7. Branch control (PRD §F8, single-target model for issue_fix)

- **`issue_fix` (single-target model):** a single **target branch** picker (the PR base). The worktree is based on that same branch, so the PR diff is exactly the agent's fix and merges cleanly by construction. This supersedes PRD §F8's two-selector design ("source `main`, target `development`"): diverged source/target produced PRs that smuggled source-only commits into the target or silently conflicted.
- **Other task types (freeform):** a **source branch** (the worktree base) and a **target branch** (the PR base) remain available.
- **`pr_review`:** branch pickers are hidden — the review worktree checks out the PR head, so branches are irrelevant.

## 8. Publish (PRD §F9)

- **Default: auto-publish** — on task completion, push the branch and open a PR. Title = agent's `.jalebi/pr.md` first `# <title>` line, falling back to `🦦 Jalebi: <first prompt line>` (or `🦦 Jalebi task`); body includes the agent's `.jalebi/pr.md` body (or the raw prompt as fallback) + `Closes #N` when an issue was referenced + the Jalebi brand footer (`🦦 Opened by [Jalebi](…) by [Samosa AI](…)`) + `Co-authored-by: Jalebi <jalebi@samosa-ai.com>`. All templates live in `apps/server/src/jalebi/messaging.py` — see `docs/14-messaging-strategy.md` for the full templates and invariants (no task IDs, no `localhost`, no internal URLs in external posts).
- **Configurable:** global `auto_publish: true|false`; when `false` (or auto-publish fails), the UI shows a **"Publish"** button (push + open PR).
- **No-op gate:** publishing (auto or manual) requires the task branch to have **at least one commit ahead of the target** — a `done` run where the agent made no commits never opens an empty PR, and manual publish refuses with "nothing to publish".
- **Sync-before-push + conflict detection:** before pushing, Jalebi fetches origin and **merges `origin/<target>` into the task branch** (`merge_origin_into`) so the PR is up to date with target's progress and mergable. A **conflict aborts the merge**, surfaces the conflicting files ("PR would conflict with `<target>`: file1…"), sets the task to `needs_approval`, and pushes/opens nothing. The user resolves via a follow-up asking the agent to merge `origin/<target>` and resolve, then publishes again.
- **PR updates on follow-ups:** follow-ups amend the same branch; the existing PR is updated by the push (publish reuses the existing PR number) — never a second PR for the same task.
- **Issue comments on new PR only:** the "Jalebi opened a pull request for this issue" comment is posted **only when a PR is newly created**, never when re-publishing to an existing open PR (follow-up pushes stay silent).

## 9. Cleanup / prune policy (PRD §F12)

- **Implemented:** artifact retention (`artifact_ttl_days`, startup prune). 
- **Planned (not implemented):** delete task worktrees for `done` tasks after a TTL (default 7 days) unless a PR is still open; `git worktree prune` on restart to recover orphaned worktrees.

## 10. Reference

- PRD §F8 (branch selection), §F9 (publish), §F12 (storage/prune), §17.2 (no `gh` CLI).
- Implementation: `src/jalebi/git_workspace.py` (+ tests in `tests/test_git_workspace.py`). Publish/PR creation is handled by the task-queue step (uses `push_branch` + the GitHub client).
## 11. Per-task worktree bootstrap

- After `create_worktree`, `worktree_bootstrap.bootstrap_worktree(wt, agent_md)` writes into the worktree root:
  - `opencode.json` — `permission.bash` rules **deny `gh`** (`gh`, `gh *`, `/usr/bin/gh*`, `command gh*`, …); project config overrides the user's global opencode config.
  - git identity — `user.name Jalebi`, `user.email jalebi@localhost` (commits are never authored by a stray local account).
  - `AGENTS.md` — task context + hard rules (no gh, no forks, push only to `origin`, write `.jalebi/pr.md`/`.jalebi/review.md`, **never commit `.jalebi/`**, **never commit the marked Jalebi `AGENTS.md` section**, only update existing docs). A repo that **tracks its own `AGENTS.md` keeps its content** — Jalebi's section is appended inside `<!-- jalebi:start -->…<!-- jalebi:end -->` markers (idempotent to re-bootstrap, stripped by `remove_guard`).
  - **shared `info/exclude`** — the mirror's common gitdir gets `.jalebi/`, `/opencode.json`, `/AGENTS.md` (root-anchored) so bootstrap files never show in `git status`, are never swept by `git add .`, and never appear as artifacts. The worktree `.gitignore` is **never mutated** (no tracked-file delta can leak into a PR).
  - a **pre-commit hook** in the mirror's common hooks dir that rejects any staged `.jalebi/` path, a staged `opencode.json`, and a staged `AGENTS.md` still containing the `jalebi:start` marker (with `git restore` recovery instructions) — invisible to the PR, covers all worktrees, and blocks even an explicit `git add -f`.
- The agent subprocess env carries the owner-PAT git credentials (`GIT_CONFIG_*` http.extraHeader → Basic `x-access-token`), commit identity vars, `JALEBI_GITHUB_TOKEN`, and strips `GH_TOKEN`/`GITHUB_TOKEN` + empty `GH_CONFIG_DIR` so `gh` can never authenticate. Inherited `GIT_CONFIG_*`/`GIT_DIR` state is stripped and `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` pinned so the parent shell cannot redirect the agent's git.
- `remove_guard` restores a pre-existing `AGENTS.md` (marker block stripped), removes `opencode.json`, and — only when this is the **last live worktree** of the mirror — removes the shared `info/exclude` lines and the hook.

## 12. Review worktrees (PRD §F7)

- `create_review_worktree(task_id, full_name, pr_number)` fetches `refs/pull/<n>/head` into the mirror (works for same-repo **and** fork PRs without touching the fork) and checks it out **detached** into `ws/task-<id>-review`. Reviewers read/validate but can never push.
- `list_branches(full_name)` lists `origin/*` from the mirror (task-form branch pickers).
- The same `opencode.json`/identity bootstrap applies to review worktrees.
