# 07 — Screening

> **Scope:** Screening engine, scheduler (cron), baseline dedup, findings, and ntfy notifications. Update this file for any screening work.

> **Status: IMPLEMENTED — Phase 2.** Ships with the starter catalog, a built-in cron scheduler (no dependency), HEAD-baseline dedup, structured findings, in-app history + ntfy notify, and "new task from finding".

---

## 1. Design principle (PRD §F10)

**Screening finds and notifies; it never acts.** No screening auto-opens issues, opens PRs, or starts fix tasks. The user converts findings into work via **"New task from finding"** (single) or a **batch** (select several findings → **"Open new task"**), which opens the Tasks page **New-task form with the prompt injected** (one finding, or one combined prompt for a batch) — backend, model, agent, branches, and publish settings are chosen in the form like any freeform task (`type: "freeform"`, `publish_mode: "manual"` prefilled).

## 2. Schema & tables (PRD §10)

- `screenings(id, repo_id FK, name, system_prompt, cadence_cron, scope_branch?, enabled, notify_ntfy, created_at, updated_at)`
- `screening_runs(id, screening_id FK, head_sha?, status, started_at?, finished_at?, findings_json?, output_json?, error?)`

Findings are stored as JSON on each run (`findings_json`), not a separate table. See `docs/02`.

## 3. Scheduler (cron, no dependency)

- `jalebi/screening.py::ScreeningScheduler` — a daemon thread (`jalebi-screening`) started in `app.main()` next to the queue. Wakes every **60s** and runs due screens **one at a time** (screening is low-frequency; it never competes with the task queue's concurrency).
- Cadence matching uses `jalebi/cron.py`, a minimal 5-field cron matcher (`minute hour dom month dow`; supports `*`, lists, ranges, steps). No new dependency (PRD Goal #10).
- **Time zone:** cadence is matched against the app's wall clock (`jalebi/clock.py::now`) — the **configured timezone** setting, defaulting to the machine's **local** zone. A cron like `30 1 * * *` fires at 1:30 **local**. (The earlier build matched UTC, so non-UTC owners' cadences never fired.) The `timezone` setting (`local` or an IANA name) is a live setting on the Settings page.
- **Baseline dedup:** a screen skips a tick when its last run (`done`) audited the **same HEAD**. A **failed** run is NOT a valid audit — it is retried on the next tick, but only after a cooldown (`FAILED_RETRY_COOLDOWN_SECONDS`, 1h) so a persistent failure isn't hot-looped and a transient flake never permanently silences a screen. "Run now" (`POST /api/screenings/<id>/run`) forces a run regardless. A crash between run-row insert and terminal commit reconciles at startup (`reconcile_stale_runs` marks orphaned `queued`/`running` rows `failed`), so restarts never double-audit or leave forever-`running` rows.
- **Concurrency:** screens are serialized by the scheduler, and a per-screen lock in the engine refuses a manual "Run now" that would race a scheduler tick on the same screen (no double-run) — a held lock answers **409 synchronously**, and a lost post-peek race records nothing (a distinct `ScreeningBusyError`, never a ghost failed row). The lock holder re-checks the screen still exists (closes the delete-during-preflight race). Scheduled ticks record their preflight failures as failed runs too.

## 4. Run execution (read-only)

Each screening run:

1. Resolves the repo's bound PAT account (`secrets.resolve_token`); no account → the run fails with a clear error.
2. Ensures the mirror, resolves the branch HEAD (`GitWorkspace.current_remote_sha`).
3. Creates a **detached, read-only worktree** at that HEAD (`GitWorkspace.create_detached_worktree`), with the `gh`-guard `opencode.json` written in (so the agent can never push or act via `gh`).
4. Drives the opencode adapter with the screen's `system_prompt` + a structured contract: *"return your findings as a single JSON array"*. Screens can pin a **backend** (`cli`, default `opencode`) and a **model** (optional); the engine resolves `get_adapter(screen.cli or "opencode")` and passes `model=screen.model or None`. Before building the prompt, the engine assembles the **known-open context** (`get_open_findings`): still-open findings from the last 3 done runs of every screen on the same repo (running screen first, dealt fingerprints excluded, cross-run duplicates dropped, 20 items / bounded title/file/screen-name + 400-char text excerpts) rendered as a fenced UNTRUSTED block instructing the agent not to re-report the same issue. Every rendered value is control-character-neutralized, JSON-quoted, and rejected if it resembles the closing delimiter; a transient context-query failure simply omits this best-effort aid and lets the audit continue.
5. Streams events to a per-run SSE bus (`/api/screenings/runs/<id>/events`), masking all output with the run masker (PATs + `secret_patterns`). The raw message accumulation is bounded (`MAX_OUTPUT_CHARS`).
6. **Watchdog:** each run has an absolute timeout (`default_timeout_minutes`) and a no-output stall timer (`stall_timeout_seconds`, both live settings); a hung agent is killed (process group) and the run marked `failed`, so a stuck audit can never block the scheduler thread or leave a forever-`running` row.
7. Parses the findings JSON (`screening.parse_findings_strict`, defensive: fenced-block tolerant, string-literal-aware bracket matching, non-array → `[]`), **masks each finding's string fields**, stores `findings_json` + masked `output_json`, and marks the run `done` (or `failed` on agent error / run error / **unparseable output** — garbage is never a clean `done`, which would watermark the HEAD and suppress the next tick).
8. **Best-effort ntfy push** when `notify_ntfy` and findings exist — a summary of the count + first findings, **minus already-dealt findings** (re-reported accepted risks never nag again). A dead ntfy server never fails the run.
9. Removes the worktree in a `finally`.

**Dealt state (server-side):** findings are marked dealt via `POST /api/screenings/dealt` (`{screen_id, fps}`, idempotent; `POST /dealt/reopen` reopens, `POST /dealt/import` is the one-time browser migration, `GET /dealt?screen_id=` lists) and stored in `screening_dealt` (see `docs/02`). A fingerprint must be canonical compact JSON and name the request's `screen_id`; SQLite `ON CONFLICT DO NOTHING` resolves each row independently, so one concurrent duplicate cannot discard other rows in the same request. Dealt drives three behaviors: inbox hide/show, the rerun context above, and notification filtering. The legacy per-browser localStorage set is import-once migrated on first Screenings visit, then the server is authoritative.

**Security posture:** screening audits potentially **untrusted** repository code — the agent runs **without the repo PAT** (`_build_agent_env(None)`), so even a prompt-injected audit agent cannot use a privileged GitHub token (the mirror/worktree are prepared by Jalebi before the run). Findings from the audit are treated as untrusted when they become task prompts ("New task from finding" labels the finding text `UNTRUSTED input` and caps its length; `publish_mode` stays `manual`). `delete_screen` refuses while a run is in flight.

**Findings shape:** `{severity: critical|high|medium|low, title, file?, line?, detail?, recommendation?}`. Severity is normalized (unknown → `medium`); string fields are capped (`file`/`title` 500, `detail`/`recommendation` 4000) since findings are untrusted agent output that flows into task prompts. Run errors are masked before persistence/logging (F17).

## 5. API

| Endpoint | Behavior |
|----------|----------|
| `GET /api/screenings/templates` | The built-in starter catalog (7 screens) — the UI instantiates these against a repo. |
| `GET /api/screenings` | List screens, each with a lightweight `latest_run` summary (status/counts/when — no findings blob). |
| `POST /api/screenings` | Create (strict types: real booleans/ints/strings; validates repo connected + cron syntax). |
| `GET /api/screenings/<id>` | Screen detail. |
| `PUT /api/screenings/<id>` | Update, present-key: only sent keys change; explicit `null` clears scope/branch/backend/model; switching `cli` without a new `model` drops the stale pin. |
| `DELETE /api/screenings/<id>` | Delete (cascades runs; refused while a run is in flight). |
| `POST /api/screenings/<id>/run` | **Run now** — 409 if a run is already in flight; otherwise asynchronous (returns 200 immediately), forces past baseline dedup; preflight failures are recorded as failed runs (masked). The card waits for the new run row before refreshing. |
| `GET /api/screenings/<id>/runs` | Run history (findings included). |
| `GET /api/screenings/findings` | Unified newest-first findings inbox across screens (`limit` ≤ 200, `severity`, `screen_id` filters) — read-only fan-out with screen/repo/run context; paged SQL-bounded scan (pages until `limit` fills under a severity filter, 2000-run cap), coerced fields. |
| `GET /api/screenings/runs/<id>/events` | SSE stream of a run's live events (unknown ids 404). |
| `GET /api/screenings/dealt?screen_id=` | Dealt fingerprints for a screen (404 unknown screen). |
| `POST /api/screenings/dealt` | Mark `{screen_id, fps}` dealt (idempotent; strict validation). |
| `POST /api/screenings/dealt/reopen` | Reopen dealt `{screen_id, fps}` (POST, not DELETE-with-body). |
| `POST /api/screenings/dealt/import` | One-time browser-migration alias of mark. |

## 6. UI

/screenings: an unread **badge** on the nav tab (client-side last-seen; visiting the tab clears it); a Screens | **Findings** tab toggle (**Findings is the default**) — the inbox lists recent findings across all screens (severity + text + screen filters, expandable detail) with **checkbox batch selection** (select-all-visible, one combined task prompt, bulk Mark dealt) and hides **dealt** findings by default (a finding counts as dealt once a task is created from it — marked server-side only after the New-task form's create succeeds, never at handoff — or when marked dealt manually; toggle reveals + reopens; server-authoritative with a legacy localStorage one-time import, optimistic UI with rollback on failure); a **health banner** when screens are failing or disabled; screen cards (repo-qualified `owner/repo · name`, repo+branch scope, cadence, enabled/notify dots, **latest-run summary** — status pill, finding count, when, inline error — **Run now** with inline errors, **History**, **Edit**, **Delete** disabled mid-run); a create/edit form with the **starter-template picker** (the chosen template stays visible), a **scope-branch dropdown** (from `GET /api/repos/<id>/branches`, failures surfaced), **cron presets** plus the editable field, optional **Backend/Model** pins (model resets when the backend changes, load failures surfaced), and the Enabled/Notify toggles. Run history shows errors before the empty state, **polls fast (5s) while live and backs off (30s) when idle**, refreshes the card when a run goes terminal, pages beyond 10 runs, and renders findings by severity with per-finding checkboxes plus a bulk bar — **"New task from finding"** (and batch **"Open new task"**) navigate to the Tasks New-task form with the prompt injected (single prompt, or one concatenated prompt with a per-finding UNTRUSTED section each; a batch must share one repo and one effective target branch or the button explains why it is disabled) — the prompt stays **editable in the form before creation** (PRD false-positive rule). Screen names render repo-qualified (`owner/repo · name` via `qualifiedScreenName`) everywhere they appear — filter dropdown, inbox rows, cards, health banner, delete confirm, batch prompts, and Mission Control tickers — so identical names on different repos stay distinguishable; stored names never change. Load failures surface per section; a loading state precedes the empty state. The cross-page handoff is location-state (`ScreeningHandoff`: prefill + finding fingerprints + id), consumed one-shot with a nonce remount and a replace-clear so refresh never re-injects; cloning a legacy `screen_finding`/`triggered` task normalizes to `freeform` so the type select stays valid.

## 7. Reference

- PRD §F10 (screening), §F12 (screening_runs table), §13 (screening scheduler tested with fake clocks).
- `jalebi/cron.py`, `jalebi/screening.py`, `jalebi/routes/screening.py`.
- Tests: `tests/test_cron.py`, `tests/test_screening.py`, `tests/test_api_screening.py` (strict parsing, stale recovery, nullable clears, model-drop on backend switch, preflight-failure runs + masking, SSE 404, summary lists, strict input types).
