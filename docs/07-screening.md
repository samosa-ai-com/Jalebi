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
- **Baseline dedup:** a screen skips a tick when its last terminal run (`done`/`failed`) audited the **same HEAD** (the audited `head_sha` is the dedup watermark). "Run now" (`POST /api/screenings/<id>/run`) forces a run regardless.

## 4. Run execution (read-only)

Each screening run:

1. Resolves the repo's bound PAT account (`secrets.resolve_token`); no account → the run fails with a clear error.
2. Ensures the mirror, resolves the branch HEAD (`GitWorkspace.current_remote_sha`).
3. Creates a **detached, read-only worktree** at that HEAD (`GitWorkspace.create_detached_worktree`), with the `gh`-guard `opencode.json` written in (so the agent can never push or act via `gh`).
4. Drives the opencode adapter with the screen's `system_prompt` + a structured contract: *"return your findings as a single JSON array"*.
5. Streams events to a per-run SSE bus (`/api/screenings/runs/<id>/events`), masking all output with the run masker (PATs + `secret_patterns`).
6. Parses the findings JSON (`screening.parse_findings`, defensive: fenced-block tolerant, string-literal-aware bracket matching, non-array → `[]`), **masks each finding's string fields**, stores `findings_json` + masked `output_json`, and marks the run `done` (or `failed` on agent error / run error).
7. **Best-effort ntfy push** when `notify_ntfy` and findings exist — a summary of the count + first findings. A dead ntfy server never fails the run.
8. Removes the worktree in a `finally`.

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

`/screenings` (moved from the ComingSoon placeholder to the main nav): screen cards (name, repo+branch scope, cadence, enabled/notify dots, **Run now**, **History**, **Delete**); a create/edit form with the **starter-template picker**; and per-run history with findings rendered by severity, each with a **"New task from finding"** button that creates a prefilled `screen_finding` task.

## 7. Reference

- PRD §F10 (screening), §F12 (screening_runs table), §13 (screening scheduler tested with fake clocks).
- `jalebi/cron.py`, `jalebi/screening.py`, `jalebi/routes/screening.py`.
- Tests: `tests/test_cron.py`, `tests/test_screening.py`, `tests/test_api_screening.py`.
