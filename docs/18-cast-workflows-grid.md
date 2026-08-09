# 18 — Cast, Workflows & Grid Dashboard (Future)

> **Status: DESIGN ONLY — not approved for implementation.** This document
> captures design decisions and a phased plan from an extended design session.
> It is the "potential future plan" — subject to change. Do not implement any
> of it without re-approval and a re-validated scope per `AGENTS.md` §2.1.

> **Scope:** Per-thread cast (primary + helpers + reviewers), reusable workflows,
> the Inbox-as-helm control-panel landing page, the dynamic 4×4 thread grid,
> and the multi-agent orchestration foundations. Phases 0–1 (current state)
> remain untouched.

---

## 1. Guiding principles

1. **One workspace, not seven pages.** Current nav splits Repos / GitHub /
   Agents / Triggers / Settings; this collapses them into surfaces inside a
   single workspace anchored on **Inbox**.
2. **Everything is a Thread.** A task, a reviewer pass, an "address reviewers"
   round, a screening finding — all are the same primitive: an agent session
   with a prompt, a live timeline, a diff, artifacts, and a conversation.
3. **GitHub is the truth; Jalebi is the cockpit.** Linked objects (issue, PR,
   comments, checks) are the frame; Jalebi's activity is the response layer.
4. **Multi-agent means *cast*, not *queue*.** Reviewers are agents on a PR,
   just like the fixer. The UI surfaces them as collaborators around a shared
   object (PR, branch, plan).
5. **Explainability by construction.** Every thread has a *Why this ran* card,
   a *What it did* timeline, a *What changed* diff, and a *What it said*
   summary. The agent never acts in shadow.
6. **Subtle-futuristic aesthetic.** Modern ops dashboard (Linear / Plane /
   Vercel vibe) with telemetry aesthetic — mono numerals, arc gauges, brand-mark
   spiral gauge. On-brand, restrained, distinctive. **Not** literal sci-fi HUD.
7. **Stable cell contract, variable cell content.** The grid cell shape stays
   the same as cast/state capabilities grow — more chips inside, more states
   light up, no redesign.

---

## 2. Information architecture (proposed)

Replace top nav with grouped entries; single entry point is **Inbox**.

```
[Jalebi ▸ bridge]   Inbox   Threads   Repos   Agents ▸ (Catalog / Workflows)   Automations   Settings
```

| Section                       | What lives here                                                        |
| ----------------------------- | ---------------------------------------------------------------------- |
| **Inbox**               | Default landing. Gauges + Grid + Telemetry.                            |
| **Threads**             | All agent sessions (queued + historical), filterable. Renames "Tasks". |
| **Repos**               | Connected repos, accounts, branches, webhook status.                   |
| **Agents → Catalog**   | Catalog CRUD.                                                          |
| **Agents → Workflows** | Saved casts.                                                           |
| **Automations**         | Triggers + Screenings + auto-reviewer toggle.                          |
| **Settings**            | Concurrency, env vars, ntfy, secrets, grid prefs.                      |

---

## 3. The generalized **Cast** concept

The unifying data model. One JSON shape, two surfaces.

### 3.1 Cast shape

```jsonc
// tasks.cast_json (and workflows.cast_json)
[
  { "role": "primary",  "agent_slug": "build-bot"   },
  { "role": "helper",   "agent_slug": "sec-bot",  "step": 4 },
  { "role": "helper",   "agent_slug": "perf-bot", "step": 7 },
  { "role": "reviewer", "agent_slug": "bug-bot"  }
]
```

### 3.2 Cast member roles

| Role                                             | When                             | What it does (today vs future)                                    |
| ------------------------------------------------ | -------------------------------- | ----------------------------------------------------------------- |
| `primary`                                      | Always one                       | Main agent.**Today.**                                       |
| `helper`                                       | Optional, scoped to a step index | Sub-agent called by primary during that step.**Future.**    |
| `reviewer`                                     | PR threads                       | Reviewer in detached worktree, posts PR comments.**Today.** |
| `planner` / `critic` / `improver` (future) | Orchestration patterns           | Reserved.                                                         |

### 3.3 Schema additions (proposed)

- `tasks.cast_json TEXT NULL` — JSON list per the shape above.
- `tasks.workflow_id INTEGER NULL` — pointer to the workflow this thread
  inherited from (metadata; thread is independent of the workflow surviving).
- `workflows(id, name, description, cast_json, deleted_at, created_at, updated_at)` — new table.
- FK-less by design (consistent with `tasks.agent_id`): SQLite batch rebuild
  hazard for FK-referenced parents (per Step 37 lessons). Validation in the
  service layer; a deleted-after-creation agent falls back to default build.

### 3.4 Per-thread + workflow-reusable (UX decision)

- **Per-thread**: every thread has its own `cast_json`. Editing it mid-run is
  allowed (with confirmations for running members).
- **Saved as workflow**: any cast can be graduated into a named workflow via
  "Save as workflow…" in the cast editor.
- **Workflow library**: under **Agents → Workflows** tab. CRUD with usage count.
- **Soft delete**: workflows are never hard-deleted (per UX decision). Threads
  snapshotted the cast on creation, so a deleted workflow leaves them intact.

---

## 4. Cast editor + workflow library

### 4.1 Cast picker (at thread creation)

Shown after the user picks the anchor (repo/branch/issue/PR):

```
WORKFLOW
  ○ Custom (build cast below)
  ● auth-refactor-flow ▾
    "build + sec helper at step 4 + perf helper at step 7; 7-step plan; manual PR"

PRIMARY
  [ build-bot ▾ · opus-4.1 · general · enabled ]

HELPERS (run alongside primary, scoped to a step)
  [ + Add helper · step [4 ▾] ]
   · sec-bot · step 4 · "verify no regression"   [×]
   · perf-bot · step 7 · "review hot paths"      [×]

REVIEWERS (PR threads)
  [ + Add reviewer ]
   · bug-bot                                     [×]

☐ Save this cast as a new workflow
  [ name: auth-refactor-flow ]
  [ description … ]
```

Behavior:

- **Custom** = user picks agents manually.
- **Use saved workflow** = cast filled from workflow; individual members can
  be overridden inline (chip dims to "modified").
- **Save as workflow** = optional checkbox at creation; the cast graduates
  into a workflow that appears in the picker next time.

### 4.2 Cell action: Edit cast

Existing threads have an "Edit cast" action on the cell (or in the lifter).
Opens the same picker, scoped to that thread. Changes mid-run:

| Change                        | Behavior                                       |
| ----------------------------- | ---------------------------------------------- |
| Add helper                    | Queued for named step.                         |
| Remove not-yet-started helper | Silently removed.                              |
| Remove running helper         | Confirm → cancel session.                     |
| Add reviewer (PR thread)      | New reviewer thread kicks off.                 |
| Remove reviewer mid-run       | Confirm → cancel.                             |
| Save current cast as workflow | Appears in library.                            |
| Apply a different workflow    | Replaces cast — confirm if reviewers mid-run. |

### 4.3 Workflows library

Lives at **Agents → Workflows** tab (keeps nav tidy). Same CRUD pattern as
catalog agents:

```
WORKFLOWS                                          [+ New]
────────────────────────────────────────────────────────────────────
┌─ auth-refactor-flow ──────────── build-bot + 2 helpers ─────────┐
│  "Build + sec helper at step 4 + perf helper at step 7."        │
│  Used by 3 threads                                               │
│  [Edit cast]  [Duplicate]  [Delete]                              │
└─────────────────────────────────────────────────────────────────┘
┌─ pr-full-review ──────────────── build-bot + 4 reviewers ────────┐
│  "Full review pass: security, perf, bug, style."                │
│  Used by 14 threads                                              │
└─────────────────────────────────────────────────────────────────┘
```

Each card: name, description, cast summary, **usage count**, edit/duplicate/delete.

---

## 5. The Inbox — the helm

### 5.1 Layout

Three layers:

```
┌── helm (top bar) ─────────────────────────────────────────────┐
│ brand · nav · pin count · api:3456 ●                          │
├── gauges (status strip) ──────────────────────────────────────┤
│ Concurrency · Queue · Cast · Webhook · Needs-action           │
│ (each: arc gauge + number + label; subtle, glanceable)       │
├── grid (the centerpiece) ────────────────────────────────────┤
│ 4×4 (configurable) · cast cells · auto-flow · reflow         │
│ pin strip on side (pinned cells)                             │
├── telemetry (event ticker) ──────────────────────────────────┤
│ reverse-chrono feed · filter chips: all/events/agents/…      │
└───────────────────────────────────────────────────────────────┘
```

### 5.2 Brand-mark spiral gauge

Signature element. The jalebi spiral in the corner becomes a multi-arc dial
synthesizing system health. All-green = slow-spinning complete spiral; any
segment darkens when its dimension is unhealthy.

---

## 6. The Grid — the heart of the dashboard

### 6.1 Fixed-size, always-full

- Default 4×4 = 16 cells. Configurable 3×3 / 5×5 / 6×6.
- Never partially full: when a cell exits, the next queued thread slides in
  within ms. If nothing queued, the cell shows "waiting…" with a slow breath
  animation.
- Overflow indicator below: "+ N queued, ETA ~3m".

### 6.2 Cell anatomy (~280×220 on 1280-wide)

```
┌──────────────────────────────────┐
│ ● running          #a4f    ⋯  ⌘P│   ← status dot + ID + actions
│                                  │
│ Fix issue #42                    │   ← human title
│ example-smoke-repo · main               │   ← repo + branch
│                                  │
│ build-bot · opus-4.1             │   ← agent + model
│                                  │
│ 4m 12s · "adding tests..."       │   ← live timer + last message
│                                  │
│ [Cancel] [Rerun] [Open ↗]        │   ← hover-revealed actions
└──────────────────────────────────┘
```

### 6.3 Cell state machine

| State                                         | Look                                        | Trigger                                                       |
| --------------------------------------------- | ------------------------------------------- | ------------------------------------------------------------- |
| `entering`                                  | Slide-in from edge, brief glow              | New thread becomes visible                                    |
| `queued`                                    | Dimmed, slow breath, grey dot               | Waiting for worker slot                                       |
| `active` (running)                          | Bright border, fast pulse, amber dot        | Worker picked up                                              |
| `attention`                                 | Amber border + pulse                        | `needs_approval` / failed / timed_out / PR comments waiting |
| `done` (unacked)                            | Green tint, gentle fade                     | Terminal, unacknowledged                                      |
| `reading` (auto-pin)                        | Locked border (syrup-300), composer visible | User opened cell                                              |
| `pinned` (manual)                           | Same as reading + pin icon                  | User pressed ⌘P                                              |
| `acknowledging`                             | Brief fade + scale-down                     | User dismissed; about to exit                                 |
| `exiting`                                   | Slide-out, slot becomes placeholder         | About to be replaced                                          |
| `planning` (future)                         | Bright border, slow pulse, "P" glyph        | Agent emitting plan                                           |
| `step-active` (future)                      | `active` look + step counter `2/7`      | Branch work, mid-step                                         |
| `awaiting-input` (future)                   | Amber, question-mark glyph                  | Agent asked user                                              |
| `delegating` (future)                       | Subtle wave on sub-agent chip               | Primary → sub-agent                                          |
| `awaiting-approval` (between steps, future) | Amber, "checkpoint"                         | Between-step user checkpoint                                  |

**Rule:** every pulse means something. No decorative animation.

### 6.4 Cell flow

1. New thread (manual / webhook / screening / follow-up) → `queued` → slides
   into lowest-stress slot.
2. Worker picks up → `active`, no repositioning.
3. Conflict / approval / failed → `attention`, reflows to front row (FLIP).
4. Done → `done`, holds 8s (configurable), then `acknowledging` → exits.
5. User clicks before done → `reading` (auto-pin). Cell stays even if done.
6. User ⌘P → `pinned` (manual), persists across reloads.
7. User dismisses → cell exits; next high-priority queued slides in.

### 6.5 Responsive sizing

| Breakpoint | Default grid                   |
| ---------- | ------------------------------ |
| < 1024 px  | 1 column, full-width           |
| 1024–1440 | 2×2                           |
| 1440–1920 | 3×3 or 4×4 (default desktop) |
| > 1920     | 5×5 or 6×6                   |

User override in Settings → Inbox. Persisted (localStorage).

---

## 7. The PR-as-cell with cast sub-chips (multi-agent payoff)

For PR threads, the PR is the cell, not the individual thread. Cast members
become sub-chips inside the cell.

```
┌──────────────────────────────────┐
│ ● 1 reviewer active   PR #57 ⋯ ⌘P│
│                                  │
│ Fix refresh-token race           │
│ example-smoke-repo · 3 threads · main   │
│                                  │
│ 🛠 fixer  ✓ done 4m12s           │
│ 🛡 sec    ● running 1m04s       │
│ 🪶 perf   ◌ queued              │
│ 🐛 bug    ⌘ pinned (read)       │
│                                  │
│ ⚠ 4 comments awaiting address    │
│                                  │
│ [Address reviewers] [Open PR ↗] │
└──────────────────────────────────┘
```

**UX decision:** PR cell with sub-chips is the chosen shape. Reviewer threads
inherit the same cast metaphor.

---

## 8. Branch-work and the Plan view

### 8.1 Branch-work cell (today vs future)

**Today** (recommended for MVP): the cell shows the current agent's run only.
When the agent stops, the cell flips to `done` (or `awaiting-input` if the
agent asks). Follow-up resumes the same session. No plan view yet.

**Future** (with backend support): cell shows `.jalebi/plan.md` as a step list,
tracks progress through it. User can pause and inject at any step.

### 8.2 The Plan view (inside the lifter, future)

```
┌── Plan (7 steps) ──────────────────────────────────────────┐
│ 1. ✓ Read existing token-refresh logic            4m12s   │
│      🛠 build-bot · 12 reads · 0 edits                    │
│ 2. ● Implementing refresh-token rotation          12m04s │
│      🛠 build-bot · 3 edits · 14 tests · 2 failing       │
│      ↳ 🛡 sec-bot (helper, 8s) — verify no regression    │
│ 3. ◌ Add rate-limit headers                                │
│ …                                                         │
│ User checkpoint after step 4 ✓                            │
│ Auto-publish: OFF                                          │
└────────────────────────────────────────────────────────────┘
```

Each step is expandable; sub-agents appear nested; user checkpoints surface
as `awaiting-approval` state.

### 8.3 UX decision: PR timing

**Manual only** for long-running branch work — no auto-PR. Cell shows an
"Open PR from this branch" button when work is done or paused at a checkpoint.
User decides when.

---

## 9. The Lifter — the expand-in-place detail view

Click a cell → it **lifts** out of the grid into a floating detail panel.
Grid behind dims. No modal. Inside: the full Thread view (prompt card,
timeline, console, diff, artifacts, conversation, follow-up composer).

- Click outside or Esc → closes (cell stays in `reading`).
- Click another cell while one is lifted → swaps (no nested panels).
- ⌘P while lifted → manual pin.
- Status changes while lifted → cell + lift both update live.

**Pinned semantics:**

- Auto-pin (`reading`) is ephemeral.
- Manual pin (`pinned`) persists server-side; survives reload.
- No pin cap (screen size is the limit).
- **8-second auto-ack** for `done` cells (idle, not lifted, not pinned).

---

## 10. Telemetry — the bottom event ticker

Reverse-chrono feed (~50 events):

- `14:02:11  reviewer-bug-bot posted 3 comments on PR #57 · auth.py:42`
- `14:01:54  webhook matched rule 'PR auto-review' (PR #57 opened by @alice)`
- `14:00:22  thread #a4f → running · build-bot · opus-4.1`

Filter chips above: all / events / agents / github / webhooks. Click → drill to
relevant thread / delivery / finding.

---

## 11. SSE architecture

- **One global stream** at `/api/inbox/stream` pushes: grid mutations,
  telemetry events, gauge updates, per-cell summaries (status, elapsed, last
  message preview).
- **One per-cell stream** at `/api/tasks/:id/events` opens **only** when a cell
  is lifted, pinned, or in `attention`.
- Avoids 16 simultaneous EventSources. Existing per-task 500-event replay
  buffer handles reconnection.

---

## 12. Multi-agent orchestration (future)

The cast metaphor generalizes:

| Anchor           | Cast members                                                           | Status            |
| ---------------- | ---------------------------------------------------------------------- | ----------------- |
| **PR**     | Fixer + N reviewers (each in own worktree)                             | **Today.**  |
| **Branch** | Primary + sub-agents per step (same worktree, sequential or delegated) | **Future.** |
| **Issue**  | (Optional) triage agent → fixer → (later) PR                         | Partial today.    |

Stable cell contract — same UI grows into orchestration without redesign.

### Future agent-to-agent patterns

```
plan → delegate → implement → review → improve → next
```

Each step has an assigned agent; sub-agents nest; user checkpoints surface as
`awaiting-approval` state; mid-session questions surface as `awaiting-input`.

---

## 13. Phased implementation plan (proposed, not approved)

> ⚠ The user has explicitly said this plan is **not final** and may change.
> Do not begin implementation without re-approval.

### Phase A — Cast & Workflows Foundation

1. **Workflows table + API + minimal picker at thread creation.**

   - New `workflows` table; `tasks.cast_json` + `tasks.workflow_id` columns.
   - Alembic migration (no FK, consistent with `tasks.agent_id`).
   - `services/workflows.py`: CRUD + soft delete + usage count + cast validation.
   - `/api/workflows` blueprint.
   - `tasks.create_task` accepts `cast`.
   - `task_to_dict` returns `cast` + `workflow_id`.
   - Web: `Workflow` / `CastMember` types; `api.listWorkflows/createWorkflow/…`.
   - `CastPicker.tsx` component (workflow picker + "save as workflow").
   - Wire into `pages/Tasks.tsx`.
   - Tests: `test_workflows.py`, `test_api_workflows.py`, `test_tasks_cast.py`.
2. **Cast stored on tasks; visible in cell + lift.**

   - `task_to_dict` includes `cast` everywhere it's returned.
   - `CastStrip.tsx` (compact, for cells + table rows).
   - `CastPanel.tsx` (full, in TaskDetail).
   - For today: only the `primary` role is honored at run time; helpers
     stored but queued/not-started.
3. **Cast editor on existing threads + mid-run cast changes.**

   - `PUT /api/tasks/<id>/cast`.
   - Service: validate, replace cast_json, kick off new reviewer threads on
     add, cancel running reviewers on remove (with confirmation).
   - `CastEditor.tsx` modal/popover; "Edit cast" button in TaskDetail + cell.
   - Tests: `test_api_cast.py` covering add/remove scenarios.
4. **Workflows library page.**

   - Refactor `Agents.tsx` to support tabs (Catalog / Workflows).
   - `WorkflowCard.tsx` + usage stats.
   - Edit/duplicate/soft-delete with confirmation.
   - Tests: `Agents.test.tsx` extended.

### Phase B — Inbox Grid Dashboard

5. **Inbox page + Grid + cell states + auto-flow.**

   - New `/inbox` route; becomes default landing.
   - `GridCell.tsx`, `StatusStrip.tsx`, telemetry strip.
   - Cell state machine + auto-flow logic.
   - CSS keyframes for entrance/exit/pulse animations.
6. **PR-as-cell + cast sub-chips.**

   - Cell renders PR branch when task has PR.
   - Reviewer threads as sub-chips.
7. **Lifter (expand-in-place).**

   - `Lifter.tsx` + `useLifter.ts`.
   - Click outside / Esc / ⌘P behaviors.
8. **Pin + auto-pin + persistent pin state.**

   - Either `tasks.pinned_manual BOOLEAN` or a `user_pins` table (TBD — for
     single-owner, a column is sufficient).
9. **Global SSE + telemetry ticker + gauges.**

   - New `/api/inbox/stream` endpoint.
   - Telemetry ticker component.
10. **Brand-mark spiral gauge + animation polish.**

    - Spiral as multi-arc dial in the corner.

### Phase C — Future (not in this plan)

11. **In-session checkpoints + sub-agent delegation + awaiting-input.**
    - Requires backend support (agents emit structured events; queue handles
      checkpoints). UI is ready.

---

## 14. Open questions still pending

These decisions affect Phase A & B; they may change:

- **Pin storage**: `tasks.pinned_manual` column vs separate `user_pins` table
  (lean toward column for single-owner).
- **Cast JSON shape**: any `step` value or only positive integers? Empty cast
  allowed? Multiple primaries? (TBD — initial shape permits one primary.)
- **Workflow delete**: confirm soft-delete UX copy when threads still reference
  ("3 threads use this workflow — soft delete keeps them intact").
- **Cast picker memory**: "remember last cast per anchor type" — localStorage
  key shape TBD.
- **Cast on trigger-spawned threads**: trigger rules need a `cast` field (or
  workflow_id) so an auto-spawned thread has the user's intended cast.

---

## 15. What this plan does NOT change

- The backend's **single-agent run model** (per docs/06 §4). Helpers are stored
  but **not executed** until Phase C. Reviews still run in their own worktrees.
- The **catalog agent** concept (persona + skills + model pin). Workflows are a
  *composition* layer above agents; they don't replace them.
- The **trigger rule** system. Cast applies to triggered threads too, but the
  rule schema itself is unchanged in this plan.
- **Secrets / masking / local-only / single-owner** posture.

---

## 16. Reference

- Design session outputs (this document captures decisions made across many
  rounds of question/answer).
- PRD: F2 (UI), F3 (queue), F6 (catalog), F7 (reviewers), F10 (screenings),
  F11 (follow-ups), F14 (triggers).
- Existing docs: `00-overview`, `06-task-queue`, `08-ui`, `15-catalog`,
  `16-triggers`.
