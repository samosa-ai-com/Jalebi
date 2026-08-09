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
  spawn an agent run while the existing publish hangs.

Dispatch is best-effort per rule: one failing rule is recorded in the delivery
result and never fails the whole delivery.

## 4. Delivery log + replay

Every received delivery is stored in `event_deliveries` (payload + status +
result) and shown in the Triggers page's **Delivery log**. A delivery can be
**replayed** (`POST /api/webhooks/deliveries/<id>/replay`) — re-run through the
matcher — which closes the "never delivered / missed event" gap (PRD §14 risk 9).

**Replay is idempotent for every action.** A rule that already created work in
the *original* delivery (its stored `result.rules`) is skipped and reported as
`already dispatched — skipped`, so replaying never creates duplicate reviewer
tasks *or* duplicate `issue_fix`/freeform tasks (and thus no duplicate PRs). A
rule added *after* the delivery is not in the prior result and still fires —
replay re-runs the matcher with the current rule set, it just never re-runs a
rule that already acted. Live webhooks are unaffected (they are already deduped
on `X-GitHub-Delivery`).

## 5. Webhook registration

`POST /api/repos/<id>/webhook` registers a webhook on the repo via the repo's
account (`github.create_hook`, type `web`, JSON, optional HMAC secret), targeting
`<webhook_url>/webhook`; `DELETE /api/repos/<id>/webhook` unregisters. The
`webhook_url` setting is the public base GitHub can reach (a tunnel such as
cloudflared/ngrok); registration is refused with a clear error when it's unset.

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

## 7. Verification

- `tests/test_webhooks.py` — signature verify, event-context extraction, rule
  CRUD + matching (branch/author/label filters), delivery dedup, dispatch for
  all four actions.
- `tests/test_api_webhooks.py` — the `POST /webhook` endpoint end-to-end
  (reviewer-task creation, dedup, signature 403, unconnected-repo ignore),
  `/api/triggers` CRUD + validation, `/api/webhook/status`, replay, and
  registration-without-URL 409.
- `apps/web/src/pages/Triggers.test.tsx` — page status/rules/deliveries,
  rule creation, webhook registration.

See `docs/09` for how to run the suites.
