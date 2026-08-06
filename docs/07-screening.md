# 07 — Screening

> **Scope:** Screening engine, cron, baseline dedup, findings, and ntfy notifications. Update this file for any screening work.

> **Status: NOT IMPLEMENTED — Phase 2.** The content below is the PRD-derived design. No screening code exists yet. When implemented, the Python equivalent of `node-cron` is `APScheduler` (or a simple timer); the `screenings` / `screening_runs` / `findings` tables are not yet in the schema.

---

## 1. Design principle (PRD §F10)

**Screening finds and notifies; it never acts.** No screening auto-opens issues, opens PRs, or starts fix tasks. The user converts findings into work.

## 2. Screen catalog

Each screen = `{ id, name, systemPrompt, cadence (cron), scope (repo/branch), enabled, notify }`.

v1 ships a starter catalog (user-editable):

- *Security posture* — vulns, secrets, injection, authz gaps, dependency risk.
- *Dependency hygiene* — outdated/abandoned/misconfigured dependencies.
- *Dead code & cruft* — unused exports, dead branches, TODO/FIXME density.
- *Test coverage gaps* — critical paths without tests.
- *Docs drift* — README/AGENTS docs out of sync with code.
- *Performance hotspots* — obvious N+1 / heavy loops / unbounded growth.
- *Code-quality consistency* — style inconsistencies across modules.

## 3. Scheduler (cron)

- A cron scheduler (planned: `APScheduler`; PRD listed `node-cron`).
- **Baseline dedup:** store last audited `HEAD` per (repo × screen); skip if unchanged.
- Run the screen at HEAD with a **read-only prompt** (no edits, no git writes) and an optional structured-output schema for findings.

## 4. Findings model

`{ severity, title, file, line?, detail, recommendation }`

## 5. Notification

- In-app (screenings tab, unread badge).
- Optional **ntfy** push (a topic the user sets) — the owner's environment already uses ntfy.

## 6. UI

- Screenings tab to configure cadence, enable/disable, view history.
- **"New task from finding"** — opens a prefilled task (still requires the user's explicit action).

## 7. Reference

- PRD §F10 (screening), §F12 (screening_runs table), §13 (testability: screening scheduler tested with fake clocks).