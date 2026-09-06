# 21 — Phase 4 Plan: Visualization, Control & Task Insight

> **Status: APPROVED PLAN — implementation order T0 → T7, each tier gated and verified
> before the next.** This is the Phase 4 roadmap: improving how Jalebi *presents*
> ongoing and past agent work, adding better control of everything, opening the
> agent's worktree in a real IDE, viewing task files in-browser, and fixing the
> "agent asks a question, then stops" UX gap (observed on task 42).

> **Scope note:** everything is **additive**. No new auth methods, no `gh` CLI, no new
> ports, no new agent backends, no disruption to existing behavior. Backend-gated
> features default **off**; frontend features are new UI surfaces or in-place upgrades.
> Respects PRD Goal #10 (simplicity above all).

---

## 1. Goals

1. **See what's happening now** — a live, glanceable view of running tasks with
   real-time activity, elapsed time, and attention signals.
2. **Understand what happened** — runs become scannable and replayable; previous
   attempts are comparable; diffs and files are viewable in the browser.
3. **Control everything** — command palette, bulk actions, and an "open this task's
   worktree in my IDE" affordance.
4. **Fix the "agent stopped and asked for input" gap** — when a run ends with the
   agent waiting on the user (task 42, run 1), surface the agent's final message
   prominently and render it as markdown (including code blocks).

---

## 2. Guiding principles

- **Additive-only.** Every feature either hardens existing behavior (no observable
  change), is gated behind an existing/new setting that defaults off, or is a new
  frontend surface.
- **Pure functions over state.** Derived status/attention computed at read time
  (AO's "status is derived, not stored").
- **Single-owner, localhost, one port.** IDE spawning and file browsing run on the
  same machine as the browser; everything stays on the PAT vault + httpx + Flask/
  SQLite/SSE stack.
- **No dependency bloat.** One new frontend dependency (markdown rendering) is
  justified; everything else is Tailwind/CSS + existing libs.

---

## 3. Feature inventory

| Tier | Feature | Backend | Frontend | Default |
|------|---------|---------|----------|---------|
| T0 | Agent "waiting for input" detection + markdown message rendering | low | yes | on |
| T1 | Env-var blocklist, diff quality, conflict pre-check, publish guards, run SHA stamping | yes | no | on (invisible) |
| T2 | PR/CI polling observer + derived attention status | yes | yes | **off** (per-repo flag) |
| T3 | Attention filter, merge-readiness panel, diff viewer upgrade | low | yes | on |
| T4 | Dependency graph, auto-nudge, durable SSE timeline, live "running now" view | yes | yes | dep graph on; nudge **off** |
| T5 | Scrollable timeline/console + GitHub repo lists, visual polish | no | yes | on |
| T6 | IDE connector (Settings → open task in IDE) | yes | yes | **off** until configured |
| T7 | In-task file browser/viewer | yes | yes | on |

---

## 4. T0 — Agent "waiting for input" handling (task 42 lesson)

**Observed:** task 42, run 1. The agent produced a long markdown plan, ended with
*"**waiting for explicit approval** before touching anything."*, then the run exited
0 and was marked `done`. The final message was a single plain-text `<p>` in the
timeline — hard to notice, markdown (headings, tables, code) not rendered.

**Goal:** when a run ends and the agent is clearly waiting on the user, the user must
(1) *see* that the task needs their attention, (2) read the agent's final message
rendered as markdown with proper code blocks, and (3) reply in one click.

### 4.1 `waiting_input` derived flag (backend, additive)

- **Files:** `apps/server/src/jalebi/attention.py` (new, shared with T2.2),
  `apps/server/src/jalebi/tasks.py` (`run_to_dict`, `task_to_dict`).
- **Logic:** a pure helper `is_waiting_message(text: str) -> bool` matching question /
  approval-waiting patterns on the **last message step** of the latest run:
  - trailing `?`, or phrases: `waiting for`, `awaiting`, `please confirm`,
    `let me know`, `need your`, `your approval`, `should I`, `do you want`,
    `want me to`, `approval`.
- A run is flagged `waiting_input` when it is terminal (`done`/`failed`/`timed_out`/
  `cancelled`/`needs_approval`) **and** its final `message` step matches.
- Expose `"waiting_input": bool` on `run_to_dict` and on the task dict (from the
  latest run). No new status value, no migration — a derived flag only.
- **Tests:** `test_attention.py` (pattern unit tests incl. task-42's real message),
  `test_api_tasks.py` (field present and correct).

### 4.2 Markdown message rendering (frontend)

- **Files:** `apps/web/package.json` (add `react-markdown`), new
  `apps/web/src/components/Markdown.tsx`, `apps/web/src/pages/TaskDetail.tsx`.
- **Change:** render every `message`-type timeline step's `text` through a small
  `Markdown` component (`react-markdown`, GitHub-flavored-ish, no raw HTML). Code
  blocks get the existing mono dark style (`bg-ink-900/60`, `max-h-72 overflow-auto`).
  Inline code, headings, tables, lists all render. Console line rendering is
  unchanged (plain text with `›`/`⚙` prefixes — those are output lines, not
  prose).
- **Tests:** new `Markdown.test.tsx`; existing `TaskDetail.test.tsx` stays green.

### 4.3 "Waiting for you" card (frontend)

- **Files:** `apps/web/src/pages/TaskDetail.tsx`, `apps/web/src/api/client.ts`.
- **Change:** when the selected/latest run has `waiting_input`, render a highlighted
  card at the top of TaskDetail (amber accent, matching the app's syrup palette):
  - header: "Agent is waiting for your input" + the run's status + timestamp;
  - body: the **full** final message rendered via `Markdown` (code blocks intact);
  - actions: **"Reply in follow-up"** (focuses the existing follow-up composer and
    pre-fills it with the message as quoted context) and **"Open worktree"** (if
    T6 IDE is configured).
- The Tasks list (T3.1) shows the same signal as an attention dot.

---

## 5. T1 — Backend hardening & diff quality (zero observable change)

### 5.1 Env-var blocklist
- **Files:** `apps/server/src/jalebi/envvars.py`, `apps/server/src/jalebi/queue.py`
  (`_agent_env`).
- `ENV_BLOCK_LIST` mirroring Parallel Code: `PATH`, `HOME`, `LD_PRELOAD`,
  `NODE_OPTIONS`, `BASH_ENV`, `GIT_SSH_COMMAND`, `GIT_CONFIG_*`,
  `NODE_TLS_REJECT_UNAUTHORIZED`, etc. `values_for_names()` / `_agent_env()`
  drop blocked names (with a logged warning); `upsert_env_var()` and `.env`
  import reject them at write time.
- **Tests:** `test_envvars.py`, `test_queue.py`.

### 5.2 Merge-base + cherry-pick-aware diffs
- **Files:** `apps/server/src/jalebi/git_workspace.py`.
- `diff_against_base(task, full_name)` using a smarter merge-base and dropping
  patch-equivalent commits (`git log --cherry-pick --right-only --reverse`),
  collapsing to empty when the branch is already merged. Existing
  `diff_against_target` stays untouched (review/screening paths).
- **Tests:** `test_git_workspace.py`.

### 5.3 Untracked-file pseudo-diffs
- **Files:** `git_workspace.py`.
- `diff_with_untracked(worktree, base)` appends synthesized `--- /dev/null` /
  `+++ b/…` hunks for `git ls-files --others --exclude-standard` (binary-aware).
- **Tests:** `test_git_workspace.py`.

### 5.4 Predictive conflict check (`git merge-tree --write-tree`)
- **Files:** `git_workspace.py`, `apps/server/src/jalebi/routes/tasks.py`.
- `predict_conflicts(full_name, task_id, base)` runs `git merge-tree --write-tree`
  on the mirror (no worktree mutation, no abort dance), parses `CONFLICT (...)`
  → paths. New `GET /api/tasks/<id>/merge-check` → `{conflicts: [...]}`.
- **Tests:** `test_git_workspace.py`, `test_api_tasks.py`.

### 5.5 Worktree branch-mismatch guard before publish
- **Files:** `git_workspace.py`, `apps/server/src/jalebi/queue.py` (`publish_task`).
- Before any push/merge: `git symbolic-ref --short HEAD` in the worktree must equal
  `jalebi/<taskId>`; else raise a clear `PublishError` (agents can checkout/detach
  elsewhere).
- **Tests:** `test_publish_modes.py`.

### 5.6 Per-run git SHA stamping
- **Files:** `apps/server/src/jalebi/db.py` (Run), new Alembic migration,
  `apps/server/src/jalebi/queue.py` (`_prepare_run`, run end).
- `runs.git_sha_start` / `git_sha_end` (nullable) with a `-dirty` suffix when
  `git status --porcelain` is non-empty; exposed in `run_to_dict`.
- **Tests:** migration cycle green; `test_queue.py` / `test_reliability.py`.

---

## 6. T2 — GitHub polling + derived status (gated off)

### 6.1 PR/CI/review polling observer behind the existing `poll_fallback` flag
- **Files:** new `apps/server/src/jalebi/poller.py`; wire in
  `apps/server/src/jalebi/app.py` (next to `ScreeningScheduler`); `github.py`;
  `routes/repos.py` (toggle already exists).
- Daemon thread, 30 s tick, same pattern as `ScreeningScheduler`. For each connected
  repo with `poll_fallback=True` (default **false**): list open PRs with the task's
  PAT, **ETag/304 in-memory cache** (~512 FIFO), attribute PRs to tasks by
  `jalebi/<taskId>` head branch, persist normalized facts (CI rollup, review
  decision, mergeability) + `repo.last_checked_at`.
- **Tests:** new `test_poller.py` with a stubbed client; default-off path proven
  untouched.

### 6.2 Derived "attention" status
- **Files:** `apps/server/src/jalebi/attention.py` (shared with T0), `tasks.py`.
- Pure `attention_for(task, run, pr_facts) -> working | needs_you | in_review |
  ready_to_merge | done`; additive `"attention"` field on the task dict. Degrades
  gracefully when no PR facts exist. `waiting_input` (T0) feeds `needs_you`.
- **Tests:** `test_attention.py`, `test_api_tasks.py`.

---

## 7. T3 — Frontend features (additive)

### 7.1 Attention status + "Needs you" filter on Tasks
- **Files:** `apps/web/src/pages/Tasks.tsx`, `apps/web/src/types.ts`.
- New "Needs you" filter chip, attention dot on rows (StatusBadge variant), stat
  card counts `needs_you`. `waiting_input` rows (T0) get the amber dot.

### 7.2 Merge-readiness panel in TaskDetail
- **Files:** `routes/tasks.py` (new `GET /api/tasks/<id>/publish-check`),
  `TaskDetail.tsx`, `api/client.ts`.
- Server combines branch-guard + predictive conflict + commits-ahead + PR/CI facts
  → `blocked | attention | ready` card above the Publish button with per-check
  detail. Consumes T1.4/5.5/6.1.

### 7.3 Diff viewer upgrade
- **Files:** new `apps/web/src/lib/unifiedDiff.ts`, `TaskDetail.tsx`.
- Port the pure TS unified-diff parser; per-file add/del totals + status + binary
  placeholder; IntersectionObserver lazy-load per-file sections; consume T1.2/1.3
  output. Replaces the current split-on-`diff --git` logic in place.

---

## 8. T4 — Approved "bigger" items (scoped minimal)

### 8.1 Task dependency graph
- **Files:** new `task_dependencies` table + migration, `db.py`, `tasks.py`,
  `queue.py`, `types.ts`, `Tasks.tsx`.
- `task_dependencies(task_id, depends_on_id)` + `blocked` status + DFS cycle check
  on add + `has_unmet_deps` in the queue + `cascade_unblock` on completion.
  `blocked` added to `TASK_STATUSES` / check constraint (migration). UI: dep badges
  + blocked state on the task row.
- **Tests:** `test_dependencies.py`, `test_queue.py`, `test_api_tasks.py`.

### 8.2 Auto-nudge CI/review feedback (gated setting, default off)
- **Files:** new `apps/server/src/jalebi/nudger.py`, `settings.py` (`auto_nudge`,
  default false), `webhooks.py` / `poller.py` hook point, `queue.py`.
- On a check-run/review-comment webhook (or poller fact) for a task's PR: enqueue a
  follow-up to that task, **send-once deduped** by persisted signature, capped
  attempts, only when the session is idle, never into a finished task.
- **Tests:** `test_nudger.py`.

### 8.3 Durable DB-backed SSE timeline
- **Files:** new `task_events` table + migration, `events.py`, `routes/tasks.py`.
- Persist each published event `(task_id, run_id, seq, payload)` alongside the
  existing seq; replay from DB after a restart instead of the in-memory 500-buffer;
  cap + prune old rows per task.
- **Tests:** `test_sse.py`, `test_events.py`.

### 8.4 Live "running now" view — better way to watch running work
- **Files:** `Tasks.tsx` (new "Running now" panel), new
  `apps/web/src/components/RunningCard.tsx`, `api/client.ts`, optional new route
  `/running` in `App.tsx`.
- One compact live card per queued/running task: status dot, **live elapsed timer**
  (client ticker from `started_at`), last-message preview, step count, agent, PR
  link, and a small **activity sparkline** (tool-call density over the run,
  derived from step timestamps). Fed by the existing 5 s `/api/tasks` poll + per-task
  SSE (bounded ≤ concurrency EventSources). No backend change beyond T2 fields.
- **Tests:** `Tasks.test.tsx` extended.

---

## 9. T5 — Presentation fixes (approved)

### 9.1 Bounded-height scrollable Timeline + Console columns
**Bug (verified, `TaskDetail.tsx:1263–1313`):** sections are
`surface flex min-h-[24rem] flex-col p-5` with `overflow-y-auto` on the inner
`<ol>`/`<pre>` but **no bounded height**, so the columns grow with content and the
page stretches.
**Fix:** fixed-height panes with `min-h-0 flex-1` scroll regions:
- `<section className="surface flex h-[30rem] flex-col p-5">` (both panes);
- `<ol … className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-2 text-sm">`;
- `<pre … className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap pr-2 …">`;
- sticky `shrink-0` headers; existing auto-scroll toggle + `useAutoScroll` kept.
**Tests:** `TaskDetail.test.tsx` green; visual check.

### 9.2 GitHub page: per-account repo lists get max-height scrollbars
**Bug (verified `Github.tsx:252–296`):** `<ul className="divide-y divide-ink-800/70">`
renders every repo with no bound.
**Fix:** wrap each account's list in `<div className="max-h-[26rem] overflow-y-auto
rounded-md border border-ink-800/60">`, keep the count line sticky above it.
**Tests:** `Github.test.tsx` green; visual check.

### 9.3 Timeline/console visual clarity
- Step-type iconography + color coding (already partly present) extended to
  **phase-tinted accents**; a soft left rail connecting timeline dots.
- Console: sticky top shows live line count + elapsed (mono, `tabular-nums`).
- Respect `prefers-reduced-motion` for existing pulse/fade animations
  (`apps/web/src/index.css`).
- **Files:** `TaskDetail.tsx`, `index.css`.

### 9.4 Tasks table hygiene
- **Sticky table header** within a bounded scroll container on the Tasks table
  (mirrors 9.2) so long lists scroll cleanly.
- Attention dot + dep badges (T2/T4) land on rows; running rows get the live
  elapsed from 8.4.
- **Files:** `Tasks.tsx`, `types.ts`.

---

## 10. T6 — IDE connector (open a task's worktree in your IDE)

**Feature:** configure the IDE binary once in Settings; then open any task's
worktree directly in that IDE from TaskDetail (and optionally from task rows).

### 10.1 Settings: IDE section
- **Files:** `settings.py` (new keys `ide_command`, `ide_name`), `app.py`
  (validator), `Settings.tsx` (new "IDE" section).
- `ide_command` default `""` (feature off). Validator: a bare executable name or
  absolute path with no spaces/shell metacharacters (regex-guarded);
  `shutil.which()` must resolve it (or it must exist if absolute). `ide_name` is a
  display label (e.g. "VS Code", "Cursor").
- **"Detect" button** probes common binaries (`code`, `cursor`, `codium`, `nvim`,
  `vim`, `subl`, `idea`, `webstorm`, `pycharm`, `phpstorm`, `goland`) via
  `shutil.which` and pre-fills the field. A "Test open" button spawns the IDE on a
  scratch dir so the owner confirms it works.
- **API:** `GET /api/ide/status` → `{command, name, found}`;
  `POST /api/ide/test` → spawns on a temp dir, returns ok/error.
- **Tests:** `test_api_settings.py`, `test_ide.py` (validation + which).

### 10.2 Open task in IDE
- **Files:** new `apps/server/src/jalebi/ide.py`, `routes/tasks.py`, `TaskDetail.tsx`,
  `api/client.ts`.
- `POST /api/tasks/<id>/open-in-ide`:
  1. read `ide_command`; if unset → 409 "IDE not configured";
  2. resolve worktree path (`GitWorkspace.worktree_path`); if missing → 404;
  3. spawn `[command, str(path)]` detached (`subprocess.Popen`, `start_new_session`,
     `stdout/stderr → DEVNULL`, **no shell** — command is validated + `which`-resolved);
  4. return `{ok, path}`.
- Frontend: "Open in IDE" button in the TaskDetail header (and a subtle link on
  task rows when configured). Also available from the T0 "Open worktree" action.
- Security notes: command comes only from the validated setting (no per-request
  input), path is the canonical worktree path, no shell interpolation.
- **Tests:** `test_ide.py` (unset → 409; spawn called with correct argv via mock;
  missing worktree → 404), `test_api_tasks.py`.

---

## 11. T7 — In-task file browser/viewer

**Feature:** browse the task's worktree files in the browser and view text files
(markdown/JSON/code) inline; download others. Read-only in Phase 4 (editing in the
worktree is out of scope).

### 11.1 Backend file endpoints
- **Files:** new `apps/server/src/jalebi/workspace_files.py`, `routes/tasks.py`.
- `GET /api/tasks/<id>/files?path=<rel_dir>` → list entries
  `{name, path, is_dir, size, extension}` for a relative directory under the
  worktree root (default root).
- `GET /api/tasks/<id>/files/content?path=<rel_file>` → text content for text files
  (UTF-8, size-capped ~256 KB), **masked** via the task's masker; binary files →
  415 with a hint to use artifacts/download.
- **Path-safety:** resolve against the worktree root and require containment
  (`Path.resolve().is_relative_to(root)`); refuse symlinks escaping the root;
  refuse `.git` paths. No absolute paths from the client.
- **Tests:** `test_workspace_files.py` (listing, content, traversal/binary/`.git`
  rejection, masking), `test_api_tasks.py`.

### 11.2 Frontend Files panel
- **Files:** new `apps/web/src/components/FileBrowser.tsx` (+ `Markdown` from T0),
  `TaskDetail.tsx`, `api/client.ts`.
- A collapsible **Files** section in TaskDetail (below Run history / above Diff):
  - directory breadcrumb + entry list (folders first), size/extension badges;
  - click a text file → inline viewer: markdown rendered via `Markdown`,
    code/JSON in a mono pre, all within a bounded-height scroll container
    (`max-h-[24rem] overflow-auto`);
  - binary files → "binary" badge (download via existing artifact flow where
    applicable);
  - live refresh: on SSE `done`/new step for a running task, re-list the changed
    dir so the owner sees files appear as the agent works (debounced).
- **Tests:** `FileBrowser.test.tsx`; `TaskDetail.test.tsx` extended.

---

## 12. Implementation order & gating

1. **T0** (agent-waiting UX + markdown) — standalone, highest user value, no
   dependencies.
2. **T1** (backend hardening) — pure backend, invisible; run full test gate.
3. **T2** (poller + attention) — new thread, gated off; exposes fields T3 needs.
4. **T3** (attention filter, merge-readiness, diff upgrade).
5. **T4** (dependency graph → auto-nudge → durable SSE → live running view).
6. **T5** (presentation fixes — scrollbars, polish).
7. **T6** (IDE connector) + **T7** (file browser) — self-contained, can land after
   T5.

Each tier must pass the full gate before the next begins.

---

## 13. Verification & docs (AGENTS.md §2.4)

- **Server:** `uv run pytest` (in `apps/server`); `ruff check`.
- **Web:** `npm test -w @jalebi/web`; `npm run lint`; `npm run build`.
- **Migration cycle:** `alembic upgrade head` + `downgrade` sanity for every new
  migration (T1.6, T4.1, T4.3).
- **Docs to update alongside code:**
  - `docs/02-data-model.md` — new columns/tables (T1.6, T4.1, T4.3, T2 facts).
  - `docs/04-git-workspace.md` — T1.2–1.5 (diffs, merge-check, guards).
  - `docs/05-github-integration.md` — T2.1 (poller), T8.2 (nudge).
  - `docs/06-task-queue.md` — T4.1 (deps), T4.2 (nudge), T4.3 (durable timeline).
  - `docs/08-ui.md` — T0, T3, T4.4, T5, T6, T7.
  - `docs/10-security.md` — T1.1 (env blocklist), T6 (IDE spawn), T7 (path safety).
  - `docs/14-env-vars.md` — T1.1.
  - `HANDOFF.md` — end of every working session.
- Register this doc in the `AGENTS.md` docs index.

---

## 14. Non-goals (explicitly out of scope for Phase 4)

- Live agent terminal (xterm/PTY mux), per-CLI lifecycle hooks, session fork trees,
  MCP server, Docker sandboxing, mobile/LAN access, in-worktree file *editing*,
  multi-user/auth changes, `gh` CLI, new ports.
- A full 4×4 "helm" grid dashboard from `docs/18` — T4.4 is the scoped, frontend-only
  version; the full vision stays a separate design-only doc.

---

## 15. Open questions (defaults chosen; can revisit)

1. **T0 detection aggressiveness:** pattern-based heuristics may over/under-flag.
   Default: conservative pattern set (validated against task-42's real message);
   the flag is cosmetic (no status change), so mis-flagging is low-risk.
2. **T6 supported IDEs:** default auto-detect list is VS Code / Cursor / Neovim /
   Vim / Sublime / JetBrains family. Custom absolute paths allowed.
3. **T7 edit-in-worktree:** deferred to a later phase; Phase 4 is read-only viewing.
4. **8.4 placement:** "Running now" panel lives on the Tasks page (default); a
   dedicated `/running` route is optional and cheap once the component exists.

---

## 16. Post-plan additions (owner-approved scope extensions)

Four features landed on `phase-4` after the T0–T7 tiers, reviewed in the
pre-PR audit and formally folded into Phase 4 here:

### 16.1 GitHub PAT rotation (`PUT /api/github/tokens/<name>`)

Replaces a named account's token **without deleting the account's data**:
the name-keyed binding (`repos.pat_name` / `tasks.pat_name`) means
connected repos, tasks, runs, triggers, and history are untouched (locked
by test). `POST /api/github/tokens` returns **409** when the name already
exists (points at PUT) so a typo can never silently overwrite an account.
The UI shows an "update token" form per account card and warns when the
new token authenticates as a different login. A mid-run swap leaves the
already-started agent on its injected token; subsequent ops use the new
one. See `docs/05-github-integration.md` §9, `docs/08-ui.md` §11.

### 16.2 Fork-PR fix flow (PR-head worktree + fork push)

A freeform task linked to a fork PR can base its worktree on the PR head
via the `pr/<N>/head` sentinel source (`pr_head_source_number` /
`effective_diff_base`; no fork remote is ever added — the mirror fetches
`refs/pull/<N>/head`). Publishing an `update_pr` to a fork pushes
`fork-pr-<N>:<head>` with `--force-with-lease=<branch>:<head_sha>` (412
on a moved fork, never a silent clobber) and refuses with a `new_pr`
guidance error when `maintainer_can_modify == false`. Agent hard-rule
"never add remotes / never push" is unchanged — all fork writes run in
the queue process. `pr_number`/`issue_number` payload fields are strictly
validated (ints + digit strings; 400, never 500). See `docs/02`
(sentinel), `docs/04` (§7–§8), `docs/05` (fork metadata), `docs/06`.

### 16.3 Attention dismissal + cancelled-task semantics

- `POST /api/tasks/<id>/dismiss-attention` persists
  `attention_dismissed: true` (+ timestamp) in `context_json` (zero
  schema change); Tasks rows (`Dismiss`) and `WaitingCard` (`Reject
  proposal`) expose it; `POST /cancel` also accepts `needs_approval`.
- **Deliberate deviation from §4.1/§6.2:** `task.status == "cancelled"`
  evaluates to `attention == "done"` and `"cancelled"` is excluded from
  `WAITING_INPUT_STATUSES` — a user-cancelled task never demands
  attention (motivated by Task 41). This overrides the plan's
  "terminal includes cancelled" wording by owner decision.
- A dismissal is scoped to its run: `queue._prepare_run` clears it via
  `tasks.clear_attention_dismissal`, so reruns/follow-ups re-arm
  attention. See `docs/02` (`context_json`), `docs/08-ui.md` §9d.

### 16.4 IDE connector expansion

`IDE_CANDIDATES` grows to 28 popular/agentic IDEs (Antigravity, Cursor,
Windsurf, Zed, Fleet, Positron, Sublime, full JetBrains family, Emacs,
Neovim, Helix, …); `detect_all_ides()` + `GET /api/ide/detect` return
every PATH-discovered candidate. Settings shows a quick-pick grid
(one click saves `ide_command` + `ide_name`), a custom-command input
with live found ✓/✕, Test-open, and Disable. "Open in IDE" affordances:
TaskDetail header button (with transient success state), personalized
`WaitingCard` action, and `FileBrowser` panel button. Zero migrations;
existing data untouched. See `docs/08-ui.md` §9f, `docs/10-security.md`.

### 16.5 Pre-PR audit fixes (not new features)

The Codex pre-PR audit (2026-09-06) produced correctness fixes folded
into the same branch: T7 intermediate-symlink `.git` bypass (+
case-insensitive `.git`), T4.2 nudger wiring (webhook route + poller
transitions; webhook branch parsing fixed for real `{"name": …}`
payloads), T4.1 dependency badges + `blocked` StatusBadge, T4.4
one-card-per-task + card PR links (plus a `GhLink` `##N` → `#N` fix),
attention re-arm on new runs, SSE throttled prune + no-duplicate replay,
FileBrowser completion refresh + History→Files→Diff placement, and
strict `pr_number`/`issue_number` validation. Details in `HANDOFF.md`.
