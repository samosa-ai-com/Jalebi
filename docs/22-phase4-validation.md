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

## 3. T2/T3 — attention + merge-readiness + diff

- [ ] Tasks page **"Needs you"** filter chip + stat card count work;
      attention dots render on rows.
- [ ] **Dismiss** on a `needs_you` row clears the badge immediately and
      survives reload; `WaitingCard` shows **Reject proposal**.
- [ ] Merge-readiness panel (`ready` / `attention` / `blocked`) reflects
      branch/commits/conflict/CI/review state above the Publish button.
- [ ] Diff viewer shows per-file A/D/R/M/B chips + add/del stats; binary
      files show the artifact hint; long diffs lazy-load per file.

## 4. T4 — deps, nudge, durable SSE, running-now

- [ ] Create task B `depends_on` A (via API): B's row shows
      `depends on #A`; while A is unfinished and B is queued/running, B
      shows the orange **⛔ blocked** pill + `blocked` StatusBadge; when A
      completes, B flips to `queued` and runs.
- [ ] Rerun of a `blocked` task → 409.
- [ ] Auto-nudge (OFF by default): enable `auto_nudge`, let a tracked PR's
      CI fail (or deliver a failing `status` webhook) → exactly one
      follow-up enqueued on the idle task; no second nudge for the same
      SHA; cap of 3 per task.
- [ ] Restart the server mid-run, reload the tab with `?after_seq=N` →
      timeline backfills from the DB with **no duplicated events**.
- [ ] With 5+ queued/running tasks, the Running-now panel shows **one card
      per task** (scroll-bounded), each with elapsed timer, step count,
      last-message preview, backend tag, activity sparkline, and a working
      **PR link**.

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
