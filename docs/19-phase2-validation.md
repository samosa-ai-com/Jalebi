# 19 — Phase 2 Validation Checklist (manual QA)

> **Scope:** a step-by-step manual checklist for validating the Phase 2 features — **screening** (cron scheduler, baseline dedup, findings, ntfy, "new task from finding"), **commit statuses / merge gating**, and **diff-view + run-history polish** — through the UI at `http://127.0.0.1:2052`. It complements `docs/12-ui-validation.md` (Phase 0) and `docs/17-phase1-validation.md` (Phase 1) — run those first to confirm earlier phases still work, then this one for the new features. Expected behavior is given per check so you can tick things off as you go.

---

## 0. Prerequisites

- [X] **You are on the `phase-2` branch** (`git branch --show-current` → `phase-2`). Phase 2 was developed on a separate branch so `main` stays the Phase-0/1 release. It is **not yet merged** — do not test on `main`.
- [X] Server is running on the Phase-2 code: `./stop.sh && ./start.sh`, then `http://127.0.0.1:2052/api/health` returns `{"status":"ok"}`. (`JALEBI_PORT` overrides the port.)
- [X] A GitHub PAT is bound to the test repo (GitHub page → account green). Recommended test account: `example-account`; connected repo: `example-account/example-test-repo`. Screening fails with a clear error if no account is bound.
- [X] The web UI is built: `npm run build` (the built `apps/web/dist` is what Flask serves).
- [X] **Commit-status verification uses the PAT, never `gh`** — `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN" …` (Section 8).

**Quick automated gate first** (should be all green before manual QA):

```
cd apps/server && uv run pytest            # 513 passed (as of the Phase-2 commits)
cd .. && npm test -w @jalebi/web           # 51 passed
npm run typecheck && npm run lint && npm run build
```

---

## 1. Shell & navigation

| #   | What to expect                                                                                                          | How to test                       | Pass |
| --- | ----------------------------------------------------------------------------------------------------------------------- | --------------------------------- | ---- |
| 1.1 | **Screenings** is now a solid nav item (no longer dimmed / "coming soon").                                        | Look at the top bar.              | ☐Y  |
| 1.2 | Clicking**Screenings** opens the real Screenings page (not a placeholder).                                        | Click Screenings.                 | ☐Y  |
| 1.3 | No browser console errors on Screenings, Repos, or a task detail page.                                                  | DevTools → Console on each page. | ☐Y  |
| 1.4 | The Repos page rows show a**statuses: on/off** toggle per connected repo (Phase 2 commit-status opt-in, PRD F15). | Open Repos.                       | ☐Y  |

---

## 2. Screenings page (`/screenings`) — list & cards (PRD F10)

| #   | What to expect                                                                                                                                                                                                            | How to test                                   | Pass                                                                                 |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------ |
| 2.1 | Empty state: "No screens yet" with a "Create your first screen" prompt.                                                                                                                                                   | Open Screenings with no screens.              | ☐Y                                                                                  |
| 2.2 | Each screen card shows: name, the repo (`owner/repo` + `· branch` or `· default`), the cadence cron (mono), two status dots (green = enabled, amber = ntfy-on), and **Run now / History / Delete** buttons. | Create a screen (see 3) and look at the card. | ☐Y<br />It is present, but the status bars for NTFY show yellow even when it is on. |
| 2.3 | The header copy states the design principle: "Screening finds and notifies — it never acts."                                                                                                                             | Read the page subtitle.                       | ☐Y                                                                                  |

---

## 3. Create / edit form

| #   | What to expect                                                                                                                                                                                                                                                                                                                                         | How to test                                                                                                                                  | Pass                                                                                                                                                                                                                                                                                                     |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3.1 | **New screen** opens the form with a **Starter template** dropdown listing all 7 built-in templates (Security posture, Dependency hygiene, Dead code & cruft, Test coverage gaps, Docs drift, Performance hotspots, Code-quality consistency). Picking one pre-fills name, cron, and system prompt.                                        | `+ New screen` → pick "Security posture" → name/cron/prompt fill in.                                                                     | ☐When I select any element of the list from the starter template, it does not show that it is selected. However, it changes all other things in the form. So it is working, but just the selected starter template list name is not showing. You can also pick a starter screen even after picking one. |
| 3.2 | Form fields: Name, Repo (connected repos),**Scope branch** (blank = default), **Cadence (5-field cron)**, System prompt, **Enabled** and **Notify on findings (ntfy)** checkboxes.                                                                                                                                             | Create a screen against the test repo with a valid cron (e.g.`0 6 * * 1`).                                                                 | ☐The scope branch should be a list of branches to select from, not just a free text field.<br />The cron entry is not something not everyone knows the syntax of it, so it's better if you actually provide some option to select from to create the cron pattern.                                      |
| 3.3 | **Validation is surfaced inline (400s, no 500s):** a bad cron (e.g. `bogus` or 4 fields) → error `invalid cadence_cron: …`; missing name / repo / system prompt / cadence → inline "name, repo, system prompt, and cadence are required".                                                                                                 | Try a bad cron → red error, form stays open. Try clearing the name → inline error.                                                         | ☐Y<br />Obviously, you will have to change it and make it more user-friendly.                                                                                                                                                                                                                           |
| 3.4 | **Edit is currently not reachable from the UI** — the screen card only offers **Run now / History / Delete** (no Edit button), and the form always opens in create mode. The backend `PUT /api/screenings/<id>` works (repo lock + validation), so edits must go through the API today. **Flagged as a known limitation (§10).** | Confirm there is no Edit button on the card; optional: update via`curl -X PUT http://127.0.0.1:2052/api/screenings/<id>` with a JSON body. | ☐Y<br />At least add an option to enable and disable the notification and the screening. Otherwise, it is useless.                                                                                                                                                                                      |
| 3.5 | Creating against a**disconnected** repo or one with **no bound PAT** is refused at create/run time with a clear error (not a 500).                                                                                                                                                                                                         | Optional: create against a repo with no account → 400 with a clear message.                                                                 | ☐I haven't tested this.                                                                                                                                                                                                                                                                                 |

I want you to add an option to be able to select the model and backend for this as well.

And the schedule actually did not work. You can look at the dead code and cruft screening action I created. It was supposed to run at 1:30 AM but it did not work.I think the reason why it did not work is the time zone. So basically, you need to use the local time zone for everything on the app. But it looks like you are using some other time zone. 

However, when I created a new card for security posture and clicked on Run Now, it worked.

---

## 4. Run now + History

> **Note:** "Run now" is **asynchronous** — the card's History loads on open/re-open. After clicking Run now, open (or re-open) **History** to watch the run appear (`running` → `done`/`failed`). The run is a real read-only audit against the branch HEAD, so it takes as long as the agent needs.

| #   | What to expect                                                                                                                                                                                              | How to test                                                                                                | Pass                                                                                                                                                                                                                                                                    |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4.1 | Clicking**Run now** returns immediately and the History expands with a run whose status goes `running` → `done`.                                                                                 | Create a screen → Run now → History shows the run stream.                                                | ☐Y                                                                                                                                                                                                                                                                     |
| 4.2 | A**done** run with findings shows each finding as: severity chip (`critical`/`high`/`medium`/`low`, color-coded), title, `file:line` (mono) when present, detail, and "Recommendation: …". | Run a screen whose audit finds something (or add a finding yourself).                                      | ☐Y                                                                                                                                                                                                                                                                     |
| 4.3 | A run with**no findings** shows "No findings." (and the run is still `done`).                                                                                                                       | Run a screen against a clean/trivial repo.                                                                 | ☐Y<br />It shows no findings even when it is running. But I would expect it to only show the findings or no findings after it completes the task.<br />Also, the history status does not change from ready to done automatically. I need to manually refresh the page. |
| 4.4 | A run that fails (no bound PAT, disconnected repo, agent error) shows`failed` with the error text in the run row — never a stuck `running` or a misleading `done`.                                   | Run against a repo with no PAT bound (or revoke it) → run`failed`, error shown.                         | ☐I haven't tested this. I hope it will work.                                                                                                                                                                                                                           |
| 4.5 | Each run row shows the audited HEAD (first 12 chars) and the started timestamp; History shows the most recent runs first (newest on top).                                                                   | Look at the History list.                                                                                  | ☐Y                                                                                                                                                                                                                                                                     |
| 4.6 | **Read-only guarantee:** the audit worktree is detached and read-only — the agent cannot push, open PRs/issues, or modify the repo. No PR, issue, or branch is ever created by a screening run.      | Run a screen, then check the repo on GitHub → nothing changed; no PRs/issues.                             | ☐Y                                                                                                                                                                                                                                                                     |
| 4.7 | **Delete** asks for confirmation and removes the screen **and its runs** (cascade).                                                                                                             | Delete a throwaway screen → card disappears, no runs remain (re-create the same screen → fresh History). | ☐Y                                                                                                                                                                                                                                                                     |
| 4.8 | **Live SSE (optional):** during a run, the events endpoint streams masked events (connect / step / message / stream_end).                                                                             | `curl -N http://127.0.0.1:2052/api/screenings/runs/<run_id>/events` while a run is in progress.          | ☐Y                                                                                                                                                                                                                                                                     |

---

## 5. Baseline dedup + the scheduler (cron)

> The scheduler is a daemon thread that wakes **every 60s** and runs enabled screens whose cron matches the current (UTC) clock. **Baseline dedup:** a screen skips a tick when its last terminal run (`done`/`failed`) audited the **same HEAD**. **"Run now" always forces** a run past the dedup watermark.

| #   | What to expect                                                                                                                                                                                                                   | How to test                                                                                         | Pass                     |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------ |
| 5.1 | Set the cron to`* * * * *` (every minute), enabled, on the test repo, and wait two ticks (~2–3 min) with **no new commits** → exactly **one** run in History (the second tick is deduped against the same HEAD). | Create the screen, note the time, wait ~3 minutes, open History → only one run row,`done`.       | ☐Y                      |
| 5.2 | After a**new commit** lands on the audited branch, the next tick creates a **second** run (new HEAD).                                                                                                                | Push a trivial commit to the repo → wait a tick → History gains a new run with a new HEAD prefix. | ☐Y                      |
| 5.3 | **Run now** always creates a run even with no new commit (forces past dedup).                                                                                                                                              | Click Run now twice back-to-back → two run rows appear.                                            | ☐Y                      |
| 5.4 | A**disabled** screen never runs on the scheduler (and is skipped in `_due_screens`).                                                                                                                                     | Toggle Enabled off → wait ticks → no new run.                                                     | ☐Y                      |
| 5.5 | **Restart** (optional): stop/start the server → the scheduler daemon restarts and continues ticking (a previously-due screen runs on the next tick; the dedup watermark survives restart).                                | `./stop.sh && ./start.sh` → wait a tick → due screens still run (or are deduped as expected).   | ☐I haven't tested this. |

---

## 6. Findings → "New task from finding"

| #   | What to expect                                                                                                                                                                                                                                           | How to test                                                                 | Pass |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ---- |
| 6.1 | Each finding has a**New task from finding** button that creates a `screen_finding` task with a **prefilled prompt** (`Fix this <severity> finding from the "<screen>" screen in <file>:<line>: <title> … <detail> … Recommended: …`). | Click it on a finding → alert "Created a screen_finding task."             | ☐Y  |
| 6.2 | The new task appears in the Tasks queue with type**Screen finding**, and its **Publish mode is manual** (it will not auto-open a PR — screening never auto-acts, PRD F10/F9).                                                               | Open Tasks → the new row; open the task detail → Publish shows`manual`. | ☐Y  |
| 6.3 | The task's**Target branch** is the screen's scope branch (or the repo default when blank).                                                                                                                                                         | Open the task → Branch reads`<target> ← default` (or the scope branch). | ☐Y  |
| 6.4 | **"Screen finding" is also a selectable type** in the New task form (label `Screen finding`), behaving like freeform (manual publish).                                                                                                           | Open the New task form → Task type dropdown includes`Screen finding`.    | ☐Y  |

---

## 7. ntfy notifications + masking

| #   | What to expect                                                                                                                                                                                                                                                                                                                                   | How to test                                                                                                                                                               | Pass                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| 7.1 | With`ntfy_topic` set (Settings → Notifications) and the screen's **Notify on findings** on, a `done` run **with findings** pushes an ntfy notification: title `Jalebi: N finding(s) — <screen> (<repo>)`, body with a bolded severity + title per finding (first 10), `tags: warning`, and a click URL to `/screenings`. | Run a screen that produces findings → check the ntfy channel.                                                                                                            | ☐Y                                               |
| 7.2 | **No findings → no push.**                                                                                                                                                                                                                                                                                                                | Run a screen with empty findings → no notification.                                                                                                                      | ☐Y                                               |
| 7.3 | **A dead/unreachable ntfy server never fails the run** — the run still ends `done` and the push is best-effort.                                                                                                                                                                                                                         | Point`ntfy_topic` at a dead host and run a findings-producing screen → run `done`, no crash.                                                                         | ☐Y                                               |
| 7.4 | **Masking:** any value matching a configured `secret_patterns` (e.g. `AKIA[0-9A-Z]{16}`) or a known PAT that appears inside a finding's title/detail/recommendation is redacted to `***` in the stored findings and the masked run output.                                                                                           | Add a`secret_patterns` value, put the matching string in the audited repo's code so the agent reports it → open History → finding shows `***`, never the raw value. | ☐I haven't tested this, but I hope it will work. |

---

## 8. Commit statuses / merge gating (PRD F15)

> **Mechanism:** commit statuses (PAT-writable), not check-runs. Only `issue_fix` and `pr_review` tasks on repos with the **statuses: on** toggle report statuses. Contexts: `Jalebi / fix` (issue_fix) and `Jalebi / review` (pr_review). A status is keyed by `(sha, context)` — posting the same context again **replaces** GitHub's status (update, don't duplicate).
>
> **Why they matter (two purposes):** (1) **Merge gating** — add the contexts to branch protection and GitHub blocks merging until Jalebi's status is green; (2) **informational signaling** — the dot tells anyone on the PR that Jalebi has already handled this commit, so they won't re-trigger a review. The `pending`-at-start is a pr_review behavior (posted on the PR head at run start); issue_fix posts only at publish time.

### Setup

1. [X] Open **Repos** → flip the test repo's toggle to **statuses: on** (green).
2. [X] Create an **Issue fix** task on that repo (pick a real open issue) → it auto-publishes a PR.

### W-C1 — issue_fix lifecycle: pending → success

1. [ ] While the task is running, open the **GitHub PR** → the commit status line shows `Jalebi / fix` as **pending** (or check via the API below).
2. [ ] When the task finishes `done` and publishes, re-check → the status for the pushed `jalebi/<taskId>` head is **success**.
3. [ ] Verify via API (never `gh`):

```bash
sha=$(curl -s -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN" \
  "https://api.github.com/repos/example-account/example-test-repo/pulls/<PR#>")
curl -s -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN" \
  "https://api.github.com/repos/example-account/example-test-repo/commits/<head_sha>/status"
# → a statuses[] entry with context "Jalebi / fix" and state success
```

### W-C2 — pr_review: review context on the PR head

1. [ ] Create a **Review PR** task on an open PR of the enabled repo (or assign a reviewer).
2. [ ] At run start the PR head shows `Jalebi / review` → **pending**; when the review posts and the run is `done`, it becomes **success**.
3. [ ] A **failed** review run (e.g. the agent errored) → the same context becomes **failure**, **not** success (the terminal status is posted *after* review posting).

### W-C3 — state mapping

| Task outcome             | GitHub state |
| ------------------------ | ------------ |
| running / needs_approval | `pending`  |
| done                     | `success`  |
| failed / timed_out       | `failure`  |
| cancelled / interrupted  | `error`    |

1. [ ] Run one `failed` fix task (e.g. force an agent error) → status `failure`.
2. [ ] Cancel a running fix task → status `error`.

### W-C4 — follow-up replaces, doesn't duplicate

1. [ ] On the successful fix task, send a follow-up that commits to the PR → when done, the same `(head, Jalebi / fix)` entry is updated to `success` again — there is still exactly **one** status for that context (no second entry).

### W-C5 — opt-in and type gating

1. [ ] A repo with the toggle **off** reports no statuses even for `issue_fix`/`pr_review` (the GitHub commit status section stays empty).
2. [ ] **freeform** and **screen_finding** tasks on an enabled repo never report a status (only fix/review contexts exist).

### W-C6 — UI + best-effort

1. [ ] A task that set a status shows a **commit status** chip (amber, "commit status") in its detail header, linking to the GitHub commit/PR.
2. [ ] **Best-effort contract:** a status API failure (bad scope, GitHub down) never fails the underlying task — the task still ends `done`; the failure is only logged.

---

## 9. Diff view + run history (M3)

> Run against a task with at least two runs (e.g. run a fix, then a follow-up).

| #   | What to expect                                                                                                                                                                                                                                                                                                                                                    | How to test                                                                                          | Pass |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ---- |
| 9.1 | **Run history** — Task detail with >1 run shows a **Run history** list (not just a dropdown). Each row: `#seq`, status badge, start time, duration (a finished run shows `Xm Ys`; a running one ticks), and `diff`/`N artifact(s)` markers where applicable. Clicking a row switches the timeline, console, artifacts, and diff to that run. | Open a task with ≥2 runs → the Run history list appears; click an older run → its logs/diff load. | ☐Y  |
| 9.2 | **Diff file label + stats** — a run that captured a diff shows a friendly file label (e.g. `src/app.ts` instead of the raw `diff --git` line), per-file `+n`/`−n` counts, and a total `N files +n −m`. Renames render as `a → b`; new/deleted files show their path only.                                                                   | Open the Diff section of a code task's run → check the labels and totals.                           | ☐Y  |
| 9.3 | **Collapse** — a multi-file diff collapses each file under its label; a single-file diff is auto-open. The raw `diff --git` header is still visible inside each file block.                                                                                                                                                                              | Expand/collapse the file blocks; confirm the raw header line is present inside.                      | ☐Y  |
| 9.4 | **Regression** — a run with no diff shows no Diff section; the empty/loading states render without error.                                                                                                                                                                                                                                                  | Open a run with no captured diff → no Diff section, no console errors.                              | ☐Y  |

---

## 10. Known limitations (do NOT expect these yet)

- **No in-app unread badge** — the PRD's "unread badge" idea is not implemented; findings live in the screen's run History only.
- **Screening runs aren't editable from the UI** — the screen card has **no Edit button** and the form always opens in create mode; only `PUT /api/screenings/<id>` can update a screen (Repo stays locked on update, which is by design).
- **Screening run events aren't streamed into the UI** — the SSE endpoint exists (`/api/screenings/runs/<id>/events`), but the Screenings page loads History on open. Re-open History to see a finished run; it does not auto-refresh while running.
- **Commit statuses only cover `issue_fix` / `pr_review`** on repos with the toggle on — freeform/screen_finding never report, and nothing is reported while a task is `needs_approval` (it stays `pending`).
- **Branch protection is GitHub-side** — Jalebi just writes the real commit statuses; you must add the `Jalebi / fix` / `Jalebi / review` contexts to branch protection yourself for merge gating to enforce them.
- **Scheduler granularity** — the daemon wakes every 60s; a cron due mid-minute fires on the next tick. Cadence uses UTC.
- **Phase 2 is not merged to `main`** — all of the above only applies on the `phase-2` branch.

---

## Done checklist

When all boxes above are ticked, Phase 2 is validated. Note any failing check (page, step, expected-vs-actual) and share it — a failing check is a bug to fix, not a test failure.
