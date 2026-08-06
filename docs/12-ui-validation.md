# 12 — UI Validation Checklist (manual QA)

> **Scope:** a step-by-step manual checklist for validating every feature Jalebi implements through the UI at `http://127.0.0.1:3456`. Use this to verify Phase 0 end-to-end. Expected behavior is given per check so you can tick things off as you go.

---

## 0. Prerequisites

- [ ] Server is running: `./start.sh` and `http://127.0.0.1:3456/api/health` returns `{"status":"ok"}`.
- [ ] A GitHub PAT is configured (GitHub page shows a green "valid & authorized" status).
- [ ] At least one repo is connected (e.g. `example-account/example-smoke-repo`) so you can create tasks.
- [ ] Recommended test account: `example-account`; the repo has a `README.md` at the root.

---

## 1. Shell & navigation

| # | What to expect | How to test | Pass |
|---|---|---|---|
| 1.1 | Top bar: jalebi spiral mark + "Jalebi" wordmark. Clicking it goes to the Tasks page. | Click the wordmark → lands on `/` | ☐ |
| 1.2 | Nav shows **Tasks · Repos · GitHub · Settings** (solid) and **Screenings · Agents · Triggers** (dimmed, "coming soon"). | Look at the top bar. | ☐ |
| 1.3 | The active page is highlighted (rounded, slightly brighter). | Click through each nav item. | ☐ |
| 1.4 | An **`api:3456` pill** with a green dot (API online) is at the top right. | Hover the dot → tooltip "API online". | ☐ |
| 1.5 | Screenings / Agents / Triggers each open a placeholder page ("Coming in a later phase…"). | Click each. | ☐ |
| 1.6 | No browser console errors on any page. | Open DevTools → Console on each page. | ☐ |

## 2. Tasks page (`/`)

### Stat cards
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 2.1 | Four cards: **Total / Running / Done / Needs review** with live counts. | Create/cancel tasks and watch the counts change (polls every 5s). | ☐ |

### Filters
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 2.2 | Filter tabs **All · Running · Done · Failed · Review**. Active tab is amber. | Click each; the table filters to that status. | ☐ |
| 2.3 | A task in `needs_approval` appears under **Review**; a `cancelled`/`timed_out`/`interrupted` task under **Failed**; `done` under **Done**. | Use tasks in various states. | ☐ |
| 2.4 | Empty-filter message ("No tasks in “running” yet.") when nothing matches. | Pick an empty filter. | ☐ |

### New task form
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 2.5 | Form has a **Repository** dropdown (connected repos), **Task type** (Freeform / Issue fix / Review PR / Screen finding), **Instructions** textarea, **Create** button. | Open the form. | ☐ |
| 2.6 | "Create" is disabled until a repo is selected and instructions are non-empty. | Try clicking with empty prompt. | ☐ |
| 2.7 | A prompt containing your PAT or a configured `secret_patterns` is **masked with `***`** when stored. | Create a task with `AKIA0123456789ABCDEF` in the prompt (with that pattern configured) → open the task → prompt shows `***`. | ☐ |
| 2.8 | On success the form clears and the new task appears in the table as `queued` → `running`. | Create a task. | ☐ |

### Table
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 2.9 | Columns: **ID, Status, Repo, Prompt, PR, Updated**. IDs are amber links to the detail page. | Look at the table. | ☐ |
| 2.10 | Status pill shows a colored dot + label (amber pulse while running). | Watch a running task. | ☐ |
| 2.11 | Repo shows as `owner/repo` (owner dimmed), prompt truncated with ellipsis. | Long prompt row. | ☐ |
| 2.12 | **Updated** is relative ("just now", "5m ago"). | Look at timestamps. | ☐ |
| 2.13 | When a task has a PR, the **PR** cell links to GitHub (`#N`); otherwise shows `–`. | Click a PR link → opens GitHub. | ☐ |

## 3. Task detail (`/tasks/:id`)

### Header & meta
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 3.1 | Back link "← Tasks", title "Task #N", status pill, `repo · model`, PR button (when a PR exists). | Open a task. | ☐ |
| 3.2 | Prompt card shows the (masked) prompt; when a phase is known, a circular **n/total** phase indicator (e.g. 1/6) with the phase name. | Open a running/done task. | ☐ |
| 3.3 | Meta grid: **Type, Branch** (`target ← source`), **Timeout, Retries**. | Look at the meta row. | ☐ |

### Actions
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 3.4 | **Cancel** shows while `running` **or** `queued`. | Open a queued or running task. | ☐ |
| 3.5 | **Re-run** shows for terminal tasks (not `cancelled`). | Open a done/failed task. | ☐ |
| 3.6 | **Publish** shows for `needs_approval` and publishes (push + PR). | Open a needs_approval task → Publish → status becomes `done`, PR link appears. | ☐ |
| 3.7 | Cancelling a running task stops it quickly and status becomes `cancelled`. | Start a task, click Cancel, watch. | ☐ |
| 3.8 | **Re-run** creates a new run (seq increments), re-runs the task, and if it commits, updates/opens the PR. | Click Re-run on a done task. | ☐ |

### Timeline & console (live)
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 3.9 | **Timeline** lists events live with timestamp, type tag, optional phase chip, and text. Colored dots per type. | Watch a running task stream events. | ☐ |
| 3.10 | **Console** streams `message` and `tool_call` lines (mono, `›`/`⚙` prefixes) with a line count. | Watch the console fill up. | ☐ |
| 3.11 | A **reload mid-stream** does not lose console lines (they're persisted). | Start a task, wait for tool calls, refresh the page, reopen → `tool_call` lines are still there. | ☐ |
| 3.12 | When the run ends, the timeline/console stay populated with the persisted run. | Let a task finish. | ☐ |
| 3.13 | Secrets are masked in the timeline/console (no raw PAT). | Any run with the token in output. | ☐ |

### Follow-up composer
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 3.14 | A **Follow-up** card (textarea + "Send follow-up" + history) appears when the latest run has a session and the task is terminal. | Open a done task. | ☐ |
| 3.15 | Sending a follow-up **live-updates the page** (no manual refresh): the task flips to running and the resumed run streams. | Send a follow-up, watch the page update by itself. | ☐ |
| 3.16 | The follow-up text appears in the history list with its time. | After sending. | ☐ |
| 3.17 | Follow-up bodies are masked too (PAT / patterns → `***`). | Send one containing a pattern. | ☐ |
| 3.18 | A follow-up that commits updates the existing PR (no duplicate PR). | Follow up on a task that already has a PR → branch gains a commit, PR number stays the same. | ☐ |

### Artifacts
| # | What to expect | How to test | Pass |
|---|---|---|---|
| 3.19 | An **Artifacts** card lists files the agent left **untracked** in the worktree, with size and a download link. | Create a task that creates a file and does *not* commit it. | ☐ |
| 3.20 | Download works (returns the file; e.g. `curl -OJ http://127.0.0.1:3456/api/tasks/N/artifacts/A/download`). | Click a download link. | ☐ |
| 3.21 | Tasks that commit everything show no Artifacts card (expected — only untracked files are captured). | A normal commit-only task. | ☐ |

## 4. GitHub page (`/github`)

| # | What to expect | How to test | Pass |
|---|---|---|---|
| 4.1 | **Connection status** card: green dot when valid, account login, token type (`classic`), and **Granted scopes** chips. | Open the page with a valid token. | ☐ |
| 4.2 | Missing scopes are listed in red under **Missing scopes**. | Use a token missing `repo` scope (if you have one). | ☐ |
| 4.3 | With **no token**, a "Connect your GitHub account" form appears (password field + "Validate & store"). | Delete `~/.jalebi/secrets.json` (and unset `JALEBI_GITHUB_TOKEN`), restart, open GitHub page. | ☐ |
| 4.4 | With a **revoked/invalid** stored token, the same form appears again so you can replace it. | Store a bad token → status shows invalid + form visible. | ☐ |
| 4.5 | Submitting an invalid token shows the real error ("Bad credentials", not just "HTTP 400"). | Paste a garbage token, submit. | ☐ |
| 4.6 | **Your GitHub repositories** lists the account's repos (auto-loads when valid) with public/private + default branch. | Open the page. | ☐ |
| 4.7 | A repo already connected shows a green **connected** chip; others show **Connect**. | Click Connect on a repo → chip appears + repo shows on the Repos page. | ☐ |

## 5. Repos page (`/repos`)

| # | What to expect | How to test | Pass |
|---|---|---|---|
| 5.1 | "Owner / repository" input + **Connect** button. Button disabled until input contains a `/`. | Type `owner/repo`. | ☐ |
| 5.2 | Connecting a valid repo adds it to the list (branch chip + id). | Connect one. | ☐ |
| 5.3 | An unknown repo (`owner/does-not-exist`) shows an error, no crash. | Try it. | ☐ |
| 5.4 | Connected repos appear in the Tasks page Repository dropdown. | Check the New task form. | ☐ |

## 6. Settings page (`/settings`)

| # | Setting | What to expect | How to test | Pass |
|---|---|---|---|---|
| 6.1 | Queue concurrency | Number input; **applies immediately** (no restart). Setting 0 **pauses** the queue (running tasks keep running, new tasks wait). | Set to 1 then 4; set to 0 and create a task → stays `queued`; set back to 4 → it runs. | ☐ |
| 6.2 | Auto-publish PRs | Toggle; when off, finished tasks don't auto-open PRs. | Toggle off → run a task → status `done`, no PR; toggle on again. | ☐ |
| 6.3 | Default timeout | Number; new tasks use it for `timeout_minutes`. | Set to 5, create a task → detail shows Timeout 5m. | ☐ |
| 6.4 | Agent backend | Select (only `opencode`). | Try to pick anything else — can't (not offered). | ☐ |
| 6.5 | Auto-retry failures | Toggle; when on, a failed task retries once automatically. | Turn on, run a failing task → it auto-runs a second time (`retry_count` = 1). | ☐ |
| 6.6 | Artifact retention | Number; applies on **next start** (startup prune). | Change it; note it says applies on next start. | ☐ |
| 6.7 | ntfy topic | Text; reserved for Phase 2 (no effect yet). | Type a topic → saved. | ☐ |
| 6.8 | Secret patterns | Textarea (one regex per line); masked from prompts/output/PRs. | Add `AKIA[0-9A-Z]{16}`, create a task containing a matching string → masked everywhere. | ☐ |
| 6.9 | Every save shows a transient "saved" indicator; invalid values show an error and are rejected. | Toggle/number + try an invalid value (e.g. `-1` concurrency). | ☐ |

## 7. End-to-end workflows (run these top to bottom)

### W1 — Full task life
1. [ ] Create a task: pick the test repo, prompt *"Append the line 'QA check' to README.md and commit with message 'qa check'."*
2. [ ] Expect the task row to show `queued` → `running` (amber pulse), then `done` (green).
3. [ ] While running: open the task → timeline and console stream live (no refresh).
4. [ ] After done: an auto-PR is created (PR cell links to GitHub); PR body contains the prompt + a `Co-authored-by` footer and a link to the Jalebi task.
5. [ ] Reload the detail page → timeline/console/artifacts still present (persisted).

### W2 — Follow-up
1. [ ] On the done task (with a PR), send a follow-up: *"Append 'QA check 2' to README.md and commit."*
2. [ ] Expect the page to update itself: status → `running`, the resumed run streams.
3. [ ] When done, the **same PR** has a second commit (no new PR); the follow-up is in the history list.

### W3 — Cancel
1. [ ] Create a long-running task, open it, click **Cancel** while `running`.
2. [ ] Expect it to stop promptly and show `cancelled`; the run row shows `cancelled`.

### W4 — Manual publish
1. [ ] Turn **Auto-publish off** in Settings; run a task that commits.
2. [ ] Expect status `done` with **no PR**.
3. [ ] Open the task → **Publish** button → click → PR created, status stays `done`, PR link appears.

### W5 — Needs-approval recovery
1. [ ] Make auto-publish fail (e.g. revoke the token temporarily) → task ends `needs_approval` (purple) with a publish error in the timeline.
2. [ ] Restore the token → click **Publish** → becomes `done` with a PR.

### W6 — Restart recovery
1. [ ] Start a long task; while `running`, kill the server (`./stop.sh` — or Ctrl-C the process).
2. [ ] `./start.sh` again → the task shows `interrupted` (not stuck `running`); a `queued` task would run after restart.
3. [ ] Re-run the interrupted task → it works.

### W7 — Timeout
1. [ ] Set a task `timeout_minutes` low (via API `POST /api/tasks {...,"timeout_minutes":1}`) or set Default timeout to 1.
2. [ ] Run a task longer than 1 minute → ends `timed_out`.

### W8 — Secrets end-to-end
1. [ ] Set `secret_patterns` to `AKIA[0-9A-Z]{16}`.
2. [ ] Create a task whose prompt contains `AKIA0123456789ABCDEF`.
3. [ ] Expect `***` everywhere: task prompt, timeline/console, **and the GitHub PR body** (open the PR to confirm).

## 8. Settings live-vs-startup cheat sheet

| Setting | Takes effect |
|---|---|
| concurrency, auto_publish, agent_cli, secret_patterns, default_timeout_minutes, retry_policy | Immediately |
| artifact_ttl_days | Next start |
| ntfy_topic | Phase 2 (no effect) |

## 9. Known limitations (do NOT expect these yet)

- **Diff viewer**, per-task model dropdown, branch selectors, screenings/triggers/agents pages — later phases.
- **Artifacts only capture untracked files** — a task that commits all its output shows no artifacts.
- **Worktree cleanup** (deleting done-task worktrees after N days) is not implemented.
- `interrupted` tasks are resumable via **Re-run** (fresh session) or **Follow-up** (if a session survived).
- Single localhost port, no auth/HTTPS — don't expose the server.

---

## Done checklist

When all boxes above are ticked, Phase 0 is validated. Note any failing check (page, step, expected-vs-actual) and share it — a failing check is a bug to fix, not a test failure.
