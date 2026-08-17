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
- **Three publish modes** (manual publish via the UI; auto-publish always uses `new_pr`):

  | Mode | What it does | When to use |
  |---|---|---|
  | `new_pr` *(default, current behaviour)* | Push `jalebi/<id>` → target, open a new PR (or reuse an existing open PR with that head). | The agent's work is a standalone change. |
  | `update_pr` | Fast-forward (or merge) `jalebi/<id>` into an existing PR's head branch, force-push with `--force-with-lease`. | The agent's commits should land on top of an existing PR (e.g. addressing review feedback or adding to a branch the user already opened). |
  | `push_branch` | Fast-forward (or merge) `jalebi/<id>` into a named branch, force-push with `--force-with-lease`. No PR interaction. | The agent's work goes onto a feature branch with no PR. |

  All three run in Jalebi's queue/server process — never in the agent subprocess. `auth_env` (git push credentials) is never applied to the agent env, so the agent still cannot push directly (see `docs/10-security.md`).

- **Smart-default for the manual Publish button:**
  - **freeform / screen_finding / triggered** — if `task.prs_json` is non-empty (the user attached a PR at creation time), the button reads `"Push to PR #N"` and dispatches `update_pr` for the first linked PR. Otherwise it reads `"Publish"` and dispatches `new_pr`.
  - **issue_fix** — always `"Publish"` and `new_pr` (its canonical purpose is opening a new PR with `Closes #N`); the other modes are still available under the **Advanced** disclosure.
  - **Advanced** disclosure exposes all three modes + a PR picker (when `prs_json` has >1 entry) + a branch text input for `push_branch`.

- **`update_pr` / `push_branch` mechanics** (`apps/server/src/jalebi/queue.py`):
  1. Fetch origin, capture the remote SHA of the target branch (`GitWorkspace.current_remote_sha`).
  2. `GitWorkspace.fast_forward_into(task_id, full_name, target_branch, token)` — try FF first, fall back to a regular merge (creates a merge commit if the branches diverged). On conflict, **abort** the merge and return the conflicting file list; the queue raises `PublishConflict` (HTTP 409).
  3. `GitWorkspace.push_existing_branch(full_name, branch, token)` — `git push origin <branch> --force-with-lease`. If the remote moved since step 1, the push is refused with `PushLeaseFailed` (HTTP 412).
  4. Append a timeline step to the latest run: `"Pushed to PR #N (existing PR head branch)."` or `"Pushed to branch \`<name>\`."`.

- **Lease safety:** `--force-with-lease` refuses to overwrite if someone else pushed to the remote branch between the fetch and the push. The UI surfaces this as a clear "remote branch moved" error so the owner can re-fetch, decide, and retry — never silent clobbering.

## 9. Cleanup / prune policy (PRD §F12)

- **Implemented:** artifact retention (`artifact_ttl_days`, startup prune). 
- **Planned (not implemented):** delete task worktrees for `done` tasks after a TTL (default 7 days) unless a PR is still open; `git worktree prune` on restart to recover orphaned worktrees.

## 10. Reference

- PRD §F8 (branch selection), §F9 (publish), §F12 (storage/prune), §17.2 (no `gh` CLI).
- Implementation: `src/jalebi/git_workspace.py` (+ tests in `tests/test_git_workspace.py`). Publish/PR creation is handled by the task-queue step (uses `push_branch` + the GitHub client).
## 11. Per-task worktree bootstrap

- After `create_worktree`, `worktree_bootstrap.bootstrap_worktree(wt, agent_md, cli=...)` writes into the worktree root:
  - a **per-CLI guard file** — `write_guard(wt, cli)`: opencode → `opencode.json` (`permission.bash` denies `gh`/`gh *`/`/usr/bin/gh*`/`command gh*`; project config overrides the user's global opencode config); codex → `.codex/rules/default.rules` (Starlark `prefix_rule` forbids `gh`, loaded for trusted projects — the codex adapter's sandbox is the confinement); claude → `.claude/settings.json` (`permissions.deny` `Bash(gh*)` variants; applies in every permission mode, skipped when the repo ships its own settings file).
  - git identity — `user.name Jalebi`, `user.email jalebi@localhost` (commits are never authored by a stray local account).
  - `AGENTS.md` — task context + hard rules (no gh, no forks, push only to `origin`, write `.jalebi/pr.md`/`.jalebi/review.md`, **never commit `.jalebi/`**, **never commit the marked Jalebi `AGENTS.md` section**, only update existing docs). A repo that **tracks its own `AGENTS.md` keeps its content** — Jalebi's section is appended inside `<!-- jalebi:start -->…<!-- jalebi:end -->` markers (idempotent to re-bootstrap, stripped by `remove_guard`). **Claude runs also get the same marked block in `CLAUDE.md`** (claude reads `CLAUDE.md`, not `AGENTS.md`).
  - **shared `info/exclude`** — the mirror's common gitdir gets `.jalebi/`, `/opencode.json`, `/AGENTS.md`, `/.claude/skills/`, `/.codex/`, `/CLAUDE.md`, `/.claude/settings.json` (root-anchored) so bootstrap files never show in `git status`, are never swept by `git add .`, and never appear as artifacts. The worktree `.gitignore` is **never mutated** (no tracked-file delta can leak into a PR).
  - a **pre-commit hook** in the mirror's common hooks dir that rejects any staged `.jalebi/` path, a staged `opencode.json`, a staged `.codex/` path, and a staged `AGENTS.md`/`CLAUDE.md` still containing the `jalebi:start` marker (with **unstage-only** `git restore --staged` recovery — a working-tree restore is forbidden mid-run because it deletes the Jalebi block that carries the task context) — invisible to the PR, covers all worktrees, and blocks even an explicit `git add -f`.
- The agent subprocess env carries the owner-PAT git credentials (`GIT_CONFIG_*` http.extraHeader → Basic `x-access-token`), commit identity vars, `JALEBI_GITHUB_TOKEN`, and strips `GH_TOKEN`/`GITHUB_TOKEN` + empty `GH_CONFIG_DIR` so `gh` can never authenticate. Inherited `GIT_CONFIG_*`/`GIT_DIR` state is stripped and `GIT_CONFIG_NOSYSTEM=1`/`GIT_CONFIG_GLOBAL=/dev/null` pinned so the parent shell cannot redirect the agent's git.
- `remove_guard` restores pre-existing `AGENTS.md`/`CLAUDE.md` (marker block stripped), removes `opencode.json`, the codex `default.rules` (and now-empty `.codex/` dirs — repo-owned `.codex` content is preserved), and the Jalebi-written `.claude/settings.json` (repo-owned settings are preserved) — and, only when this is the **last live worktree** of the mirror, removes the shared `info/exclude` lines and the hook.

## 12. Review worktrees (PRD §F7)

- `create_review_worktree(task_id, full_name, pr_number)` fetches `refs/pull/<n>/head` into the mirror (works for same-repo **and** fork PRs without touching the fork) and checks it out **detached** into `ws/task-<id>-review`. Reviewers read/validate but can never push.
- `list_branches(full_name)` lists `origin/*` from the mirror (task-form branch pickers).
- The same per-CLI guard + identity bootstrap applies to review worktrees.

---

## 13. Phase 4 T1 — diff quality + publish guards

- **`GitWorkspace.diff_against_base(worktree, base_branch)`** (T1.2) — cherry-pick-aware cumulative diff. Uses `git merge-base origin/<base_branch> HEAD` for the true common ancestor, then `git log --cherry-pick --right-only --reverse <base>..HEAD --format=%H` to drop patch-equivalent commits. Falls back to a plain two-dot range diff if there is no common ancestor. A fully-merged branch (all commits patch-equivalent) collapses to `""`. Read-only — never mutates the worktree.
- **`GitWorkspace.diff_with_untracked(worktree, tracked_diff)`** (T1.3) — appends synthesized `diff --git` / `new file mode 100644` / `--- /dev/null` / `+++ b/<rel>` hunks for each file in `git ls-files --others --exclude-standard`. Binary files (NUL byte in the first 8 KB) and files over `_UNTRACKED_DIFF_SIZE_CAP` (1 MB) are omitted. Pure read of the working tree; composes with both `diff_against_target` and `diff_against_base`.
- **`GitWorkspace.predict_conflicts(full_name, task_id, base_ref)`** (T1.4) — runs `git merge-tree --write-tree origin/<base_ref> jalebi/<task_id>` on the **mirror** (never the worktree — no worktree mutation, no abort dance). Returns `[(kind, path), ...]` parsed from the file-info lines (stages 1/2/3) + `CONFLICT (kind)` lines; empty when the merge would be clean. Requires git ≥ 2.38. Caller runs `ensure_mirror` first.
- **`GitWorkspace.assert_publish_branch(task_id)`** (T1.5) — refuses to publish when the worktree HEAD is not on `jalebi/<task_id>`. An agent may checkout/detach onto another branch; publishing would then push the wrong ref. Raises `GitWorkspaceError`; the queue's `_publish` (`queue.py:_guard_publish_branch`) is the first line of every publish path and translates it into `PublishError`. The guard runs in `_publish` (which covers both the manual `publish_task` route and the auto-publish path in `_stream_and_finish`).
- **`GET /api/tasks/<id>/diff?base=1&untracked=1`** — live diff of the worktree branch. Default (`?base=0&untracked=0`) matches the existing run-end snapshot behavior. `?base=1` switches to `diff_against_base`; `?untracked=1` appends the untracked-file hunks. Returns `{diff, base, untracked}`. Additive, zero observable change to existing pages.
- **`GET /api/tasks/<id>/merge-check`** — predictive conflict check. Runs `predict_conflicts` on the mirror (read-only); returns `{ok, conflicts: [{kind, path}]}`. Calls `assert_publish_branch` first (refuses 409 with `kind: "branch_mismatch"` when the worktree HEAD is not on the canonical branch). 404 when the task or repo is missing.
- **`GitWorkspace.rev_parse_head(worktree)`** (T1.6 helper used by the queue) — returns the full SHA of the worktree's HEAD; raises `GitWorkspaceError` when the worktree has no commits yet. The queue wraps this with a `-dirty` suffix when `git status --porcelain` is non-empty.

**New endpoints are not consumed by the existing UI today.** T3 (diff viewer upgrade) wires `?base=1&untracked=1`; the merge-readiness panel (T3.2) wires `/merge-check`. Until then the endpoints sit as additive API surface verified by the integration tests in `tests/test_api_tasks.py`.
