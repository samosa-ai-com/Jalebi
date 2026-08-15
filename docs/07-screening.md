# 07 — Screening

> **Scope:** Screening engine, scheduler (cron), baseline dedup, findings, and ntfy notifications. Update this file for any screening work.

> **Status: IMPLEMENTED — Phase 2.** Ships with the starter catalog, a built-in cron scheduler (no dependency), HEAD-baseline dedup, structured findings, in-app history + ntfy notify, and "new task from finding".

---

## 1. Design principle (PRD §F10)

**Screening finds and notifies; it never acts.** No screening auto-opens issues, opens PRs, or starts fix tasks. The user converts findings into work via the **"New task from finding"** button, which creates a `screen_finding` task (manual publish) with a prefilled prompt.

## 2. Schema & tables (PRD §10)

- `screenings(id, repo_id FK, name, system_prompt, cadence_cron, scope_branch?, enabled, notify_ntfy, created_at, updated_at)`
- `screening_runs(id, screening_id FK, head_sha?, status, started_at?, finished_at?, findings_json?, output_json?, error?)`

Findings are stored as JSON on each run (`findings_json`), not a separate table. See `docs/02`.

## 3. Scheduler (cron, no dependency)

- `jalebi/screening.py::ScreeningScheduler` — a daemon thread (`jalebi-screening`) started in `app.main()` next to the queue. Wakes every **60s** and runs due screens **one at a time** (screening is low-frequency; it never competes with the task queue's concurrency).
- Cadence matching uses `jalebi/cron.py`, a minimal 5-field cron matcher (`minute hour dom month dow`; supports `*`, lists, ranges, steps). No new dependency (PRD Goal #10).
- **Time zone:** cadence is matched against the app's wall clock (`jalebi/clock.py::now`) — the **configured timezone** setting, defaulting to the machine's **local** zone. A cron like `30 1 * * *` fires at 1:30 **local**. (The earlier build matched UTC, so non-UTC owners' cadences never fired.) The `timezone` setting (`local` or an IANA name) is a live setting on the Settings page.
- **Baseline dedup:** a screen skips a tick when its last run (`done`) audited the **same HEAD**. A **failed** run is NOT a valid audit — it is retried on the next tick, but only after a cooldown (`FAILED_RETRY_COOLDOWN_SECONDS`, 1h) so a persistent failure isn't hot-looped and a transient flake never permanently silences a screen. "Run now" (`POST /api/screenings/<id>/run`) forces a run regardless.
- **Concurrency:** screens are serialized by the scheduler, and a per-screen lock in the engine refuses a manual "Run now" that would race a scheduler tick on the same screen (no double-run).

## 4. Run execution (read-only)

Each screening run:

1. Resolves the repo's bound PAT account (`secrets.resolve_token`); no account → the run fails with a clear error.
2. Ensures the mirror, resolves the branch HEAD (`GitWorkspace.current_remote_sha`).
3. Creates a **detached, read-only worktree** at that HEAD (`GitWorkspace.create_detached_worktree`), with the `gh`-guard `opencode.json` written in (so the agent can never push or act via `gh`).
4. Drives the opencode adapter with the screen's `system_prompt` + a structured contract: *"return your findings as a single JSON array"*. Screens can pin a **backend** (`cli`, default `opencode`) and a **model** (optional); the engine resolves `get_adapter(screen.cli or "opencode")` and passes `model=screen.model or None`.
5. Streams events to a per-run SSE bus (`/api/screenings/runs/<id>/events`), masking all output with the run masker (PATs + `secret_patterns`). The raw message accumulation is bounded (`MAX_OUTPUT_CHARS`).
6. **Watchdog:** each run has an absolute timeout (`default_timeout_minutes`) and a no-output stall timer (`stall_timeout_seconds`, both live settings); a hung agent is killed (process group) and the run marked `failed`, so a stuck audit can never block the scheduler thread or leave a forever-`running` row.
7. Parses the findings JSON (`screening.parse_findings`, defensive: fenced-block tolerant, string-literal-aware bracket matching, non-array → `[]`), **masks each finding's string fields**, stores `findings_json` + masked `output_json`, and marks the run `done` (or `failed` on agent error / run error).
8. **Best-effort ntfy push** when `notify_ntfy` and findings exist — a summary of the count + first findings. A dead ntfy server never fails the run.
9. Removes the worktree in a `finally`.

**Security posture:** screening audits potentially **untrusted** repository code — the agent runs **without the repo PAT** (`_build_agent_env(None)`), so even a prompt-injected audit agent cannot use a privileged GitHub token (the mirror/worktree are prepared by Jalebi before the run). Findings from the audit are treated as untrusted when they become task prompts ("New task from finding" labels the finding text `UNTRUSTED input` and caps its length; `publish_mode` stays `manual`). `delete_screen` refuses while a run is in flight.

**Findings shape:** `{severity: critical|high|medium|low, title, file?, line?, detail?, recommendation?}`. Severity is normalized (unknown → `medium`).

## 5. API

| Endpoint | Behavior |
|----------|----------|
| `GET /api/screenings/templates` | The built-in starter catalog (7 screens) — the UI instantiates these against a repo. |
| `GET /api/screenings` | List screens, each with `latest_run` summary. |
| `POST /api/screenings` | Create (validates repo connected + cron syntax). |
| `GET /api/screenings/<id>` | Screen detail. |
| `PUT /api/screenings/<id>` | Update (name/prompt/cron/scope/enabled/notify). |
| `DELETE /api/screenings/<id>` | Delete (cascades runs). |
| `POST /api/screenings/<id>/run` | **Run now** — asynchronous (returns 200 immediately), forces past baseline dedup. |
| `GET /api/screenings/<id>/runs` | Run history (findings included). |
| `GET /api/screenings/runs/<id>/events` | SSE stream of a run's live events. |

## 6. UI

`/screenings` (moved from the ComingSoon placeholder to the main nav): screen cards (name, repo+branch scope, cadence, enabled/notify dots, **Run now**, **History**, **Edit**, **Delete**); a create/edit form with the **starter-template picker** (the chosen template stays visible), a **scope-branch dropdown** (from `GET /api/repos/<id>/branches`), **cron presets** plus the editable field, optional **Backend/Model** pins (from `/api/models`), and the Enabled/Notify toggles (reachable via Edit). Run history **polls every 5s** while the section is open, shows a **"Running…"** state for in-flight runs (never a misleading "No findings."), and renders findings by severity with a **"New task from finding"** button that creates a prefilled `screen_finding` task.

## 7. Reference

- PRD §F10 (screening), §F12 (screening_runs table), §13 (screening scheduler tested with fake clocks).
- `jalebi/cron.py`, `jalebi/screening.py`, `jalebi/routes/screening.py`.
- Tests: `tests/test_cron.py`, `tests/test_screening.py`, `tests/test_api_screening.py`.
