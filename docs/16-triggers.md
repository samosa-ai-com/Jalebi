# 16 — Event-Driven Triggers (PRD F14)

> **Scope:** The webhook listener, delivery dedup + replay, trigger rules, and
> webhook registration. Update this file for any webhook/trigger work.

---

## 1. Model: webhook-pushed, not polled

Triggering is a first-class **webhook-pushed** mechanism (PRD F14): GitHub
delivers a repo webhook event to Jalebi's local listener, which validates +
dedups + matches it against the user's **trigger rules**, then creates + enqueues
task(s) immediately. No polling. (The per-repo `poll_fallback` toggle on the
`repos` table exists for a future fallback but is not implemented.)

## 2. The listener — `POST /webhook`

Flow (sub-second — no GitHub API calls on the webhook path, PRD §13):

1. **Signature verification** — when a `webhook_secret` is configured, the
   `X-Hub-Signature-256` header is verified (HMAC-SHA256 over the raw body).
   Missing/malformed → `403`. Empty secret = check disabled (only safe on a
   localhost-only install). **The listener is exempt from the UI Basic-auth
   gate** — the webhook signature IS the webhook's auth (see `app._basic_auth_gate`).
2. **Idempotent dedup** — deliveries are deduped on `X-GitHub-Delivery`
   (`event_deliveries.github_delivery_id` is UNIQUE). A GitHub re-delivery
   returns `200 {"deduplicated": true}` and never double-runs a task.
3. **Repo resolution** — the payload's `repository.full_name` must match a
   **connected** repo; otherwise the delivery is recorded as `ignored`.
   Auto-nudge receives this resolved repository ID and requires the task's
   `repo_id` to match before recording/enqueuing a follow-up. A `jalebi/<id>`
   branch in another connected repo cannot nudge that task.
4. **Rule matching** — the event key is `X-GitHub-Event` + payload `action`
   (e.g. `pull_request.opened`), matched against the repo's **enabled** rules,
   honoring `branch_filter` (head OR base ref), `author_filter`, and
   `label_filter` (all must be present).
5. **Dispatch** — each matched rule acts (below); the delivery is recorded with
   its result for the log + replay.

## 3. Trigger rules

`trigger_rules(id, repo_id FK, event, action, branch_filter?, label_filter?,
author_filter?, agent_ids_json, custom_instructions?, enabled, created_at)`
(see `docs/02`). CRUD via `/api/triggers`.

**Events** (`event`): `pull_request.opened` / `pull_request.synchronize` /
`pull_request.reopened` / `pull_request_review` / `issues.opened` / `push`.
A rule matches the full event key (`pull_request.opened`) or the bare event —
so a bare `pull_request_review` rule fires on `submitted`/`edited`/`dismissed`
(GitHub always sends an action; exact-only matching used to drop these silently).

**Rule validation** (save-time, not runtime): `start_review` requires reviewer
`agent_ids`; `triage_issue`/`create_task` require non-empty
`custom_instructions`; `label_filter`/`agent_ids` must be lists. Updates
re-validate the effective post-update state (switching a rule to
`start_review` without reviewers is a 400). `PUT` is present-key: only sent
keys change, and an explicit `null` clears an optional field (branch, author,
instructions) — previously uncleared-forever.

**Webhook-created tasks target the repo's real `default_branch`** (not a
hardcoded `main`), and every created task is stamped with its origin
(`context.triggered_by`: delivery id, event, timestamp), which the task API
exposes and TaskDetail renders as "Started by" (the F14 origin requirement).

**Actions** (`action`):
- `start_review` — `pull_request.opened` ⇒ **each assigned reviewer from the
  catalog (kind reviewer) gets its own `pr_review` task immediately** (PRD F14
  core use case). Requires `agent_ids` (the reviewer agents).
- `triage_issue` — `issues.opened` ⇒ an `issue_fix` task (prompt from
  `custom_instructions`, issue embedded).
- `create_task` — a freeform task with `custom_instructions` as the prompt.
- `rerun_review` — re-enqueues the PR's existing reviewer tasks (a fresh review
  pass, e.g. on `pull_request.synchronize`). Re-enqueues only terminal statuses
  (`done` / `failed` / `timed_out` / `interrupted` / `cancelled`); tasks awaiting
  manual publish (`needs_approval`) are excluded — a fresh review pass would
  spawn an agent run while the existing publish hangs. Re-enqueued tasks move
  their `ReviewAssignment` back to `queued` too, so the registry never reports
  a waiting pass as finished.

Dispatch is best-effort per rule: one failing rule is recorded in the delivery
result and never fails the whole delivery.

## 4. Delivery log + replay

Every received delivery is stored in `event_deliveries` (payload + status +
result) and shown in the Triggers page's **Delivery log**. A delivery can be
**replayed** (`POST /api/webhooks/deliveries/<id>/replay`) — re-run through the
matcher — which closes the "never delivered / missed event" gap (PRD §14 risk 9).

**Replay is idempotent for work, retried for failures.** A rule whose stored
work already contains a real (non-error) entry is skipped and reported as
`already dispatched — skipped`, so replaying never creates duplicate reviewer
tasks *or* duplicate `issue_fix`/freeform tasks (and thus no duplicate PRs). A
rule whose stored work is empty or error-only produced nothing to duplicate,
so replay re-evaluates it (a transient failure gets a second chance). A rule
added *after* the delivery is not in the prior result and still fires. The
replayed outcome is **persisted back** onto the delivery (status + result), so
the log shows what actually happened — and a second replay of retried work
skips cleanly. Live webhooks are unaffected (already deduped on
`X-GitHub-Delivery`).

## 5. Webhook registration

`POST /api/repos/<id>/webhook` registers a webhook on the repo via the repo's
account (`github.create_hook`, type `web`, JSON, optional HMAC secret), targeting
`<webhook_url>/webhook`; `DELETE /api/repos/<id>/webhook` unregisters. The
`webhook_url` setting is the public base GitHub can reach (a tunnel such as
cloudflared/ngrok); registration is refused with a clear error when it's unset.

- **Adopt on conflict:** if the hook already lives on GitHub (HTTP 422 — e.g.
  after a DB reset), registration adopts it instead of 502ing forever.
- **Safe unregister:** refused without `webhook_url` (Jalebi can't tell its
  hook from another integration's, so it never guess-deletes); a successful
  listing that finds nothing of ours clears a stuck `registered` flag, while a
  failed listing keeps it.

**Reachability:** for a localhost-only install GitHub cannot reach the machine —
the tunnel is the owner's responsibility. `GET /api/webhook/status` reports the
configured URL, whether a secret is set, and each connected repo's registration
state; the Triggers page surfaces "not exposed" so the owner knows to set the
tunnel URL.

## 6. Settings

- `webhook_url` ("" — public tunnel base; validated as empty or `http(s)://`).
- `webhook_secret` ("" — HMAC secret; empty disables signature verification).

Both are regular settings (persistent, seedable, editable via the Settings page
→ **Webhooks** section).

## 7. Triggers page UX

- Rule form: per-action explainer, agent picker for every action that uses one
  (reviewers-only and required for `start_review`; any agent, first-selected
  runs the task, for `triage_issue`/`create_task`; hidden for `rerun_review`),
  prompt required inline for triage/create (hidden for rerun), branch/author/label
  hints (author/labels need PR/issue payloads), no-repo guard, inline validation
  before save (no post-submit 400 surprises), editing rule B with the form open
  on rule A remounts via `key` (no stale values).
- Rule rows show full scope chips (branch/author/labels/agents/prompt/disabled).
- Delivery rows expand to per-rule outcomes with created-task links and error
  text; `failed` is annotated `error` vs `no work`. The log filters by status +
  text and both lists scroll in capped regions (no endless page scroll).
- Webhook status: Register disabled without a tunnel URL; an unsigned-delivery
  warning shows when no secret is set; load failures surface per section.
- TaskDetail shows "Started by" (event + timestamp) for triggered tasks.

## 8. Verification

- `tests/test_webhooks.py` — signature verify, event-context extraction, rule
  CRUD + matching (incl. bare `pull_request_review` matching suffixed keys,
  branch/author/label filters), delivery dedup, dispatch for all four actions.
- `tests/test_api_webhooks.py` — the `POST /webhook` endpoint end-to-end
  (reviewer-task creation, dedup, signature 403, unconnected-repo ignore),
  `/api/triggers` CRUD + validation (missing prompt, non-list filters,
  action-combo re-validation on PUT, null-clears), `/api/webhook/status`,
  replay (persist + error/empty retry + second-replay skip), default-branch
  targeting, `triggered_by` exposure, register adopt-on-422, unregister
  refuse-without-URL + stuck-flag clearing, corrupt-result resilience,
  JSON 404 for unknown write paths, 413 on oversized bodies, and
  registration-without-URL 409.
- `apps/web/src/pages/Triggers.test.tsx` — page status/rules/deliveries,
  rule creation + inline validation, agent picker per action, stale-form
  remount, rule chips, register disabled state, secret warning, delivery
  expansion + task links, log filtering, replay outcome; `TaskDetail.test.tsx`
  covers the "Started by" line.

See `docs/09` for how to run the suites.
