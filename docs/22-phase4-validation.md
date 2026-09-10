# 22 — Phase 4 Validation Checklist (manual QA)

> **Scope:** manual UI QA for every Phase 4 feature (T0–T7) plus the four
> post-plan additions (§16) and the pre-PR audit fixes. Run against the
> `example-repo` repo (`example-owner/example-repo`) with the server on `phase-4`.
> Companion: `docs/21-phase4-plan.md` (spec), `docs/12-ui-validation.md`
> (general UI checklist pattern).

---

## 0. Prerequisites + automated gate

- [ ] Server: `uv run pytest` (in `apps/server`) — expect **828 collected**,
      all green modulo the known `test_check_runs.py:331` race flake
      (passes isolated; rerun to confirm).
- [ ] `uv run ruff check` clean.
- [ ] Web: `npm test -w @jalebi/web` — **119 passed**; `tsc --noEmit`,
      `eslint`, `prettier` clean; `npm run build` succeeds.
- [ ] Migrations: `alembic upgrade head` + `downgrade -1` + `upgrade head`
      cycle green (heads: T1.6 SHA stamping, T4.1/T4.3 deps/events/nudges).
- [ ] `./start.sh` serves API + UI on `127.0.0.1:2052`; test account PAT
      configured; `example-repo` connected.

## 1. T0 — waiting-for-input UX

- [ ] Create a task whose agent ends asking a question (or approve-plan
      style); the TaskDetail shows the amber **"Agent is waiting for your
      input"** card with the full final message rendered as markdown
      (headings, tables, code blocks).
- [ ] **"Reply in follow-up"** focuses the composer with the message quoted.
- [ ] Cancel a task mid-run → its attention becomes `done` (never stuck on
      "Needs you" — Task 41 regression check).

## 2. T1 — backend hardening (invisible; spot-check)

- [ ] Publish from a worktree whose HEAD left `jalebi/<id>` → clear
      `PublishError` (branch guard).
- [ ] `GET /api/tasks/<id>/merge-check` on a conflicting branch lists
      conflict paths.
- [ ] Run history shows per-run git SHAs (`-dirty` suffix when dirty).
- [ ] The untracked diff omits symlinks to files inside/outside the worktree,
      directory symlinks, and `.git` contents; ordinary new text files remain visible.

## 3. T2/T3 — attention + merge-readiness + diff

- [ ] Tasks page **"Needs you"** filter chip + stat card count work;
      attention dots render on rows.
- [ ] **Dismiss** on a `needs_you` row clears the badge immediately and
      survives reload; `WaitingCard` shows **Reject proposal**.
- [ ] Merge-readiness panel (`ready` / `attention` / `blocked`) reflects
      branch/commits/conflict/CI/review state above the Publish button.
- [ ] Diff viewer shows per-file A/D/R/M/B chips + add/del stats; binary
      files show the artifact hint; long diffs lazy-load per file.
- [ ] Select an older run after a follow-up changes the worktree: Diff shows
      that run's saved snapshot. Return to the latest run to see the current diff.

## 4. T4 — deps, nudge, durable SSE, running-now

- [ ] Create task B `depends_on` A (via API): B's row shows
      `depends on #A`; while A is unfinished and B is `blocked`, B
      shows the orange **⛔ blocked** pill + `blocked` StatusBadge; when A
      completes, B flips to `queued` and runs.
- [ ] Fail A's auto-publish: A becomes `needs_approval`, B stays blocked with
      its dependency pill. Failed manual publish keeps B blocked; successful
      manual publish releases B (new PR, update PR, and push branch modes).
- [ ] Rerun of a `blocked` task → 409.
- [ ] Auto-nudge (OFF by default): enable `auto_nudge`, let a tracked PR's
      CI fail (or deliver a failing `status` webhook) → exactly one
      follow-up enqueued on the idle task; no second nudge for the same
      SHA; cap of 3 per task.
- [ ] Deliver a failing status for `jalebi/<task_id>` in another connected
      repository: no follow-up or nudge quota consumed for the original task.
- [ ] Restart the server mid-run, reload the tab with `?after_seq=N` →
      timeline backfills from the DB with **no duplicated events**.
- [ ] With 5+ queued/running tasks, the Running-now panel shows **one card
      per task** (scroll-bounded), each with elapsed timer, step count,
      last-message preview, backend tag, activity sparkline, and a working
      **PR link**.
- [ ] On a long task page (tall Timeline/Console), Publish / Push-to-PR opens
      the confirm dialog **centered in the viewport** (visible without
      scrolling), not at the page center; artifact preview likewise. Escape
      and backdrop-click close both.
- [ ] Background the tab (or sleep/disconnect) mid-run for 2+ min, return:
      the timeline resumes on its own (ping heartbeat + watchdog resubscribe,
      no duplicates) with no manual refresh; if the run finished while away,
      the page shows the terminal state.

## 5. T5 — presentation

- [ ] Timeline + Console panes scroll internally (`h-[30rem]`, sticky
      titles) instead of stretching the page; console shows line count +
      elapsed.
- [ ] Long task tables + per-account repo lists scroll with sticky headers.
- [ ] `prefers-reduced-motion` kills pulse/fade animations.

## 6. T6 — IDE connector

- [ ] Settings IDE section lists detected IDEs (Antigravity/Cursor/
      Windsurf/VS Code/Zed on this machine) as quick-pick cards; one click
      saves; custom command validates live (found ✓/✕); Test-open works;
      Disable works.
- [ ] TaskDetail header **Open in \<IDE\>** opens the worktree (transient
      `Opened in \<IDE> ✓`); unconfigured → `Configure IDE…` link.
- [ ] WaitingCard action reads `Open worktree in \<IDE\>`; FileBrowser
      toolbar has its own Open-in-IDE button.

## 7. T7 — file browser

- [ ] Files panel sits between Run history and Diff; lists folders-first
      with size/extension badges; breadcrumb navigates.
- [ ] Markdown renders via Markdown; code/JSON in mono; binary shows the
      artifact hint; files containing the PAT are masked.
- [ ] `?path=.git`, `../escape`, symlink entries, and `.Git`/`.GIT`
      variants are refused (400).
- [ ] Files created by a running agent appear on SSE (debounced),
      including the final state at run completion.

## 8. Post-plan additions (§16)

- [ ] **Token rotation:** update an account's token via the GitHub page —
      connected repos/tasks/history intact; duplicate-name add → 409 with
      update hint; login-change notice shown.
- [ ] **Fork-PR flow (needs PR #7 or a fork PR on `example-repo`):** freeform →
      link PR → `PR #N head (fork:branch)` source → agent work → Publish
      pushes to the fork (requires "Allow edits from maintainers",
      else a clear `new_pr` pointer). Garbage `pr_number` → 400.
- [ ] **Dismissal re-arm:** dismiss attention, then rerun/follow-up → the
      new run's attention is live again (not stuck `done`).

## 8b. Backend-reliability + searchable-dropdowns QA (tasks 68–75 follow-ups)

- [ ] Every dropdown filters as you type (models, backends, repos, branches,
      screens, skills, timezones); keyboard Up/Down/Enter/Escape works.
- [ ] Switching backend in any form clears the model picker; a model id that
      is not in the new backend's list shows a warning instead of submitting
      silently (custom-value entry covers catalog-less backends).
- [ ] A grok-style word stream shows a handful of merged message steps, not
      hundreds; collapsed tool calls show `tool — summary` one-liners.
- [ ] Rerun dialog with a different backend/model actually runs the new
      backend (run row carries it); a rerun after attempt-cap exhaustion
      still auto-recovers once.
- [ ] Follow-up with a backend switch shows the backend badge on its row;
      a failed follow-up still appears in the follow-ups list.

## 8c. Fix failed CI + classic-token guidance (2026-09-10)

- [ ] GitHub page "Add a GitHub account" recommends a **classic `repo`** token
      (covers Actions logs) and notes fine-grained needs **Actions: read**.
- [ ] The missing-scopes help matches that recommendation.
- [ ] On a task with an open PR whose CI is failing, the Publish readiness
      panel's `ci` row shows **Fix failed CI**; it is absent when CI is green
      or no PR is linked.
- [ ] Clicking it opens the New-task form prefilled: `freeform`, same repo,
      PR linked, source `pr/<N>/head`, task's target branch, manual publish,
      and a prompt instructing the agent to fetch the failed Actions run with
      `$JALEBI_GITHUB_TOKEN`. No follow-up is posted.
- [ ] Submitting that form runs an agent that can read the failed run's logs
      (classic `repo` PAT) and updates the existing PR on publish (`update_pr`).

## 8d. Mission-control bounded-card layout (2026-09-10)

- [ ] On a wide (`xl`) screen, each Mission Control card keeps its own height:
      the right rail is stats (auto) / control shelf (0.8fr) / Kitchen wire (1.2fr).
- [ ] Enable many backends (e.g. 15+). The **Backends** card keeps its size and
      scrolls internally; the **Kitchen wire** below it keeps its height.
- [ ] Many cooks / many skills scroll inside the Cooks and Pantry cards.
- [ ] Many burners (high `concurrency`) scroll inside the shop-floor pot grid.
- [ ] No card's content pushes another card off-screen or to near-zero height.

## 9. Known limitations (do NOT expect these yet)
- `test_check_runs.py:331` may flake under full-suite load (isolated green).
- Fork push needs maintainer-edit permission; otherwise `new_pr` fallback.
- Claude Code adapter: unit-tested only (no live auth on dev machine).
- Optional plan items still out: dedicated `/running` route, task-row IDE
  link, in-worktree file *editing*.

## Done checklist

- [ ] All boxes above checked (or deviations noted with task/issue refs).
- [ ] `HANDOFF.md` updated with the validation result.
- [ ] PR description lists the Prettier-commit behavior deltas (§16.5).
