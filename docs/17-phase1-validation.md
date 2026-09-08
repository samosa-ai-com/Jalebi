# 17 — Phase 1 Validation Checklist (manual QA)

> **Scope:** a step-by-step manual checklist for validating the Phase 1 features (agent catalog, reviewer workflow, event-driven webhook triggers) through the UI at `http://127.0.0.1:3456`. It complements `docs/12-ui-validation.md` (Phase 0) — run that one first to confirm Phase 0 still works, then this one for the new features. Expected behavior is given per check so you can tick things off as you go.

---

## 0. Prerequisites

- [X] **You are on the `phase-1` branch** (`git branch --show-current` → `phase-1`). Phase 1 was developed on a separate branch so `main` stays the untouched Phase-0 release.
- [X] Server is running on the Phase-1 code: `./stop.sh && ./start.sh`, then `http://127.0.0.1:3456/api/health` returns `{"status":"ok"}`.
- [X] A GitHub PAT is configured (GitHub page → at least one green account). Recommended test account: `example-account`; connected repo: `example-account/example-test-repo` (or `example-smoke-repo`).
- [X] The web UI is built: `npm run build` (the built `apps/web/dist` is what Flask serves).
- [X] **For webhook end-to-end tests you need a public URL** (a tunnel like `cloudflared`/`ngrok`) OR you can validate the listener fully via local `curl` with a signed payload (Section 8) — GitHub simply cannot reach `127.0.0.1`.

---

## 1. Shell & navigation

| #   | What to expect                                                                                                                                                        | How to test                       | Pass |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- | ---- |
| 1.1 | Top bar now shows**Tasks · Repos · GitHub · Agents · Triggers · Settings** as solid nav items. **Screenings** is the only dimmed "coming soon" left. | Look at the top bar.              | ☐Y  |
| 1.2 | Clicking**Agents** opens the real Agents page (not "Coming soon").                                                                                              | Click Agents.                     | ☐Y  |
| 1.3 | Clicking**Triggers** opens the real Triggers page (not "Coming soon").                                                                                          | Click Triggers.                   | ☐Y  |
| 1.4 | Clicking**Screenings** still shows "Coming in a later phase…".                                                                                                 | Click Screenings.                 | ☐Y  |
| 1.5 | No browser console errors on Agents / Triggers / a task detail page.                                                                                                  | DevTools → Console on each page. | ☐Y  |

---

## 2. Agents page (`/agents`) — the catalog (PRD F6)

### List & metadata

| #   | What to expect                                                                                                                                                                                                                                                   | How to test                                                                                                                                                                                                                                        | Pass                                                                                                                                    |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 2.1 | Empty state: "No catalog agents yet" with a "Create an agent…" prompt.                                                                                                                                                                                          | Open Agents with an empty catalog.                                                                                                                                                                                                                 | ☐Y                                                                                                                                     |
| 2.2 | Each agent card shows: slug (amber, mono), a kind badge (`general` / `reviewer`), the display name, optional `cli:` / `model:` chips, skill count, a 2-line personality preview, and **Edit / Delete** buttons.                                    | Create one agent (see 2.3) and look at the card.                                                                                                                                                                                                   | ☐Y     |
| 2.3 | **Create** (`+ New agent`): slug (lowercase letters/digits with single hyphens), name, kind, optional CLI/model pins, personality markdown, skills editor (+ Add skill), custom instructions, enabled toggle. **Model is a dropdown** populated from `/api/models` (the CLI's `listModels()`) with an empty "no pin (CLI default)" option — not a free-text field. **Save** works and the card appears. | Create a reviewer agent, e.g. slug`security-auditor`, name `Security Auditor`, kind `reviewer`, personality *"You are a senior application security engineer."*, one skill, custom instructions *"Check auth, secrets, and injection."*. Leaving CLI at "default (opencode)" and Model at "no pin" must save fine. | ☐Y |
| 2.4 | **Validation errors are surfaced inline (400s, no 500s):** bad slug (e.g. `Bad Slug!`), empty name, kind not in {general, reviewer}, a skill name with `..`/`/` (path-traversal guard, e.g. `../../escape`), duplicate skill names.                | Try each invalid input → red inline error, form stays open.                                                                                                                                                                                       | ☐Y                                                                                                                                     |
| 2.5 | **Edit**: change kind/model/personality → Save → the card updates; untouched fields are preserved.                                                                                                                                                       | Edit the agent you created.                                                                                                                                                                                                                        | ☐Y                                                                                                                                     |
| 2.6 | **Clear a pin**: set a model, then clear the model field (empty) → Save → the `model:` chip disappears (an empty string clears, it can't get stuck).                                                                                                   | Set then clear the model pin via the model dropdown (see 2.2 — the pin is now a `<select>` from `/api/models`; the empty "no pin (CLI default)" option clears it).                                                                                  | ☐Y     |
| 2.7 | **Disable**: toggle **Enabled** off → the card shows a `disabled` badge. A disabled agent is **not** offered in the task form's Agent picker.                                                                                               | Disable it, then open the New task form's Agent dropdown.                                                                                                                                                                                          | ☐Y                                                                                                                                     |
| 2.8 | **Delete** asks for confirmation and removes the card. Tasks that used the agent keep their history (their `agent_id` stays; a rerun just falls back to the default build agent).                                                                        | Delete a throwaway agent.                                                                                                                                                                                                                          | ☐Y                                                                                                                                     |

---

## 3. New task form — agent picker + reviewers (F6/F7)

| #   | What to expect                                                                                                                                                                              | How to test                                                                                     | Pass |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ---- |
| 3.1 | The form's**Agent** dropdown lists the **default build agent** (placeholder) + every **enabled** catalog agent as `Name (slug)`.                                        | Open the New task form.                                                                         | ☐Y  |
| 3.2 | Selecting an agent does**not** force a model onto the task (the agent's pinned model applies at run time; the task-level Model dropdown stays whatever you chose or empty = default). | Pick an agent with a pinned model → the Model dropdown is unchanged (empty unless you set it). | ☐Y  |
| 3.3 | Selecting**Review PR** (`pr_review`) with enabled reviewer-kind agents shows a **Reviewers** checkbox group ("Each reviewer runs its own review task…").                     | Switch the type to Review PR.                                                                   | ☐Y  |
| 3.4 | Picking reviewers + a PR +**Create** creates **one task per reviewer** (all `pr_review`, each with that agent), not a single task.                                            | Create a review with 2 reviewers → Tasks page shows 2 new rows.                                | ☐Y  |
| 3.5 | Selecting reviewers on a**non-Review-PR** type is refused (the UI doesn't offer the group there; the API 400s if forced).                                                             | The group only appears for Review PR.                                                           | ☐Y  |

---

## 4. Task detail — reviewers card + address-reviewers (F7)

| #   | What to expect                                                                                                                                                                                                                                                                | How to test                                                                                              | Pass |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ---- |
| 4.1 | A task that has a PR (its own`pr_number`, or a PR referenced) shows a **Reviewers** card: `posted/running/queued` count (`N/M posted`), each reviewer row = agent name + status badge + a link to its reviewer task (`task #N`) + a GitHub PR link when posted. | Open the fix task whose PR you assigned reviewers to.                                                    | ☐Y  |
| 4.2 | The**Assign more reviewers** row lists reviewer agents **not already assigned**; clicking one creates a new reviewer task + assignment and updates the card.                                                                                                      | Click`+ <reviewer>` on the card.                                                                       | ☐Y  |
| 4.3 | Reviewers of kind`general` are **not** offered in the assign row (only `reviewer` kind).                                                                                                                                                                            | Check the card's available buttons.                                                                      | ☐Y  |
| 4.4 | A reviewer task that finished and posted shows its assignment`posted`; if it failed/timed out/cancelled, the assignment shows `failed` (never stuck `running`).                                                                                                         | Open the reviewer task's detail; watch its card on the parent.                                           | ☐Y  |
| 4.5 | **Address reviewers** button appears in the **Follow-up** composer when the task has a PR. Clicking it opens the **New-task form prefilled** (same repo, freeform, PR linked, "Address the review comments" checked) — a fresh task addresses the comments; nothing is posted until the form is submitted. | Click "Address reviewers" → land on the creation form with repo/PR/prompt/checkbox prefilled; submit → the new task's brief carries the address-reviews instruction. | ☐   |

---

## 5. Reviewer workflow — end-to-end (run top to bottom)

> Best done on a test repo with a real PR (from a prior task, or an external PR).

### W-R1 — Assign reviewers via the create form

1. [X] Create a **Review PR** task, pick a real open PR on the test repo, check **two** reviewer agents → **Create**.
2. [X] Expect **two** `pr_review` tasks queued/running (each its own agent), each opening a detached review worktree at the PR head.
3. [X] Open each reviewer task → the review streams; on `done`, the agent wrote `.jalebi/review.md`.
4. [X] When both post, the GitHub PR shows **two review comments** (the Jalebi-branded header + the review body + the ⭐ footer; body is masked — see W-R3). Jalebi never approves/merges.
5. [X] The parent task's **Reviewers** card shows `2/2 posted`.

### W-R2 — Assign from the task's PR card

1. [X] On any task with a PR, click `+ <reviewer>` in the Reviewers card.
2. [X] Expect a new reviewer task + a `queued` assignment row on the card.
3. [X] Watch it go `queued → running → posted`.

### W-R3 — Masking in review comments

1. [X] Set `secret_patterns` to `AKIA[0-9A-Z]{16}` (Settings).
2. [X] Make the agent's review mention `AKIA0123456789ABCDEF` (e.g. put it in the PR description being reviewed, or add a `secret_patterns` value to the review).
3. [X] Open the posted review comment on GitHub → the value is `***`, not raw.

### W-R4 — Address reviewers follow-up

> **Only the fixer task offers "Address reviewers".** The button appears in the Follow-up composer when a task has a PR **and** is *not* a `pr_review` task (a reviewer's session lives in a detached review worktree, so resuming it there can never push to the PR). If you don't see the button on a reviewer task, that's correct.

1. [X] After reviewers posted, open the **fix** task → Follow-up composer → **Address reviewers**.
2. [X] Expect the fixer session to resume and the timeline to show it reading the embedded review comments (look for "PR review comments to address" content) and amending the branch.
3. [X] The same PR gains the new commit (no duplicate PR) — the fixer's follow-up run auto-publishes onto the existing PR.

### W-R5 — Follow-up review runs post their review

> A `pr_review` **follow-up** must deliver too: on `done`, Jalebi reads the review worktree's `.jalebi/review.md` (fallback: the last agent message), masks it, and posts it as a PR review comment — the same path as the initial review. A `done` run that produced **no review content** is a failure, not a silent success.

1. [X] Open a reviewer task that has posted, then send it a follow-up (e.g. "expand the security section") and wait for the resume run to finish `done`.
2. [X] The GitHub PR shows a **second** review comment (the agent's amended review, Jalebi-branded + ⭐ footer), and the task's **Reviewers** card still shows `posted`.
3. [X] Run's timeline ends with "Review posted to PR #N."; the review body is masked (see W-R3).
4. [X] Regression: a reviewer task whose follow-up ends `done` **without writing** `.jalebi/review.md` or a final message shows the run/task as `failed` with a "nothing to post" error step — never a misleading `done`/`posted`.

---

## 6. Settings — Webhooks section + write-only secret (F14)

| #   | What to expect                                                                                                                                                                                                    | How to test                                                                                                                  | Pass               |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| 6.1 | A**Webhooks** card above Notifications with two inputs: **Public webhook URL (tunnel base)** and **Webhook secret (optional)**, plus a hint showing the exact delivery URL (`<url>/webhook`). | Open Settings.                                                                                                               | ☐Y                |
| 6.2 | Saving the URL works; an invalid value (not`http(s)://`) is rejected with an error.                                                                                                                             | Set`https://jalebi.example.tunnel`, then try `not-a-url`.                                                                | ☐Y                |
| 6.3 | **The secret is write-only**: after setting one, the field shows `••••••••`, and `GET /api/settings` never returns the real value.                                                              | Set a secret → refresh Settings → field shows bullets;`curl http://127.0.0.1:3456/api/settings` has no plaintext secret. | ☐Y                |
| 6.4 | Re-saving the masked placeholder does**not** wipe the stored secret; clearing the field (empty) **does** clear it.                                                                                    | Set a secret → save the bullets (unchanged) → still set; save empty → cleared.                                            | ☐Y                |
| 6.5 | **Password-protected hardening:** if `JALEBI_PASSWORD` is set but no webhook_secret, `POST /webhook` is refused with a clear error (the signature is the webhook's only auth).                          | Optional: start with`JALEBI_PASSWORD=x`, no secret → curl the listener → 403.                                            | ☐Y |
| 6.6 | **Failed-login alerts:** with a password set and `ntfy_topic` configured, a wrong-password attempt pushes an ntfy notification ("failed login attempt" + client IP). One per client per 60s; never blocks the 401. | `curl -u x:wrong -s -o /dev/null -w "%{http_code}" http://127.0.0.1:2052/api/settings` → 401 + an ntfy push; repeat within a minute → no second push. | ☐Y |

---

## 7. Triggers page (`/triggers`) — status, rules, deliveries (F14)

### Webhook status card

| #   | What to expect                                                                                                                                                                                                                      | How to test                                                                                               | Pass |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ---- |
| 7.1 | The card shows a**reachable / not exposed** pill (not exposed when `webhook_url` is empty), the configured URL, a "signature verified" note when a secret is set, and a per-repo list with **Register / Unregister**. | Open Triggers.                                                                                            | ☐Y  |
| 7.2 | With no`webhook_url` set, **Register** shows a clear error ("expose Jalebi via a tunnel… and set the URL in Settings first").                                                                                              | Click Register with an empty URL.                                                                         | ☐Y  |
| 7.3 | With a`webhook_url` set, **Register** calls the GitHub API and flips the repo to `registered` (needs the repo's account to have admin/webhook scope — the test account does).                                            | Set a URL, click Register → status flips; verify a hook exists on GitHub (repo → Settings → Webhooks). | ☐Y  |
| 7.4 | Clicking**Register** again is a **no-op** (no duplicate hook is created).                                                                                                                                               | Click Register twice → GitHub shows exactly one hook.                                                    | ☐Y  |
| 7.5 | **Unregister** removes the hook(s) matching the exact URL and flips back to `not registered`.                                                                                                                               | Unregister → GitHub shows no hook, UI shows not registered.                                              | ☐Y  |

### Trigger rules

| #   | What to expect                                                                                                                                                                                                                                                                                                                                                                           | How to test                                          | Pass |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ---- |
| 7.6 | `+ New rule` opens a form: Repo, Event (`pull_request.opened` / `.synchronize` / `.reopened` / `pull_request_review` / `issues.opened` / `push`), Action (`start_review` / `triage_issue` / `create_task` / `rerun_review`), optional branch / author / label filters, and — for `start_review` — a **Reviewers** checkbox group (kind-reviewer agents). | Open the rule form.                                  | ☐Y  |
| 7.7 | `start_review` without reviewers is rejected ("start_review requires agent_ids").                                                                                                                                                                                                                                                                                                      | Try to save a start_review rule with no reviewers.   | ☐Y  |
| 7.8 | Rules list each with event, action badge, repo, filters, agents, an Enable/Disable toggle, Edit, Delete. A disabled rule shows a`disabled` badge and never fires.                                                                                                                                                                                                                      | Create a rule → toggle it off → it shows disabled. | ☐Y  |

### Delivery log

| #    | What to expect                                                                                                                                                           | How to test                                           | Pass  |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- | ----- |
| 7.9  | The**Delivery log** lists every received webhook: status pill (`matched`/`ignored`/`received`), event (+ action), repo, time, and a **replay** button. | Send a webhook (Section 8) → a row appears.          | ☐Y  |
| 7.10 | Replaying a delivery re-runs the matcher but is **idempotent for every action**: a rule that already created work in the original delivery is skipped (no duplicate reviewer tasks *and* no duplicate `issue_fix`/freeform tasks → no duplicate PRs). A rule added **after** the delivery still fires on replay. | Replay a PR-opened delivery → no new reviewer tasks; replay an `issues.opened` delivery → no second `issue_fix` task. | ☐Y   |

---

## 8. Webhook end-to-end (no tunnel needed — drive the listener with curl)

> GitHub can't reach `127.0.0.1`, but the listener is fully testable locally. These commands send a **real** `pull_request.opened` delivery through signature verification + dedup + rules.

### Setup

1. [X] Set the `webhook_secret` in Settings (e.g. `livesecret`).
2. [X] Create a reviewer agent and a trigger rule: `/triggers` → `+ New rule` → repo `example-account/example-test-repo`, event `pull_request.opened`, action `start_review`, reviewers = your agent.

### W-W1 — Signed delivery creates a reviewer task [I'm not going to test this. I hope it works.]

1. [ ] Send a signed delivery (run this from the repo root — it uses the `phase-1` checkout; the token is read from the vault, never printed):

```bash
python3 - << 'PY'
import hmac, hashlib, json, urllib.request
payload = {
  "action": "opened",
  "repository": {"full_name": "example-account/example-test-repo"},
  "pull_request": {"number": 999, "title": "webhook smoke", "base": {"ref": "main"},
                   "head": {"ref": "feature/x"}, "user": {"login": "bob"}, "labels": []},
}
body = json.dumps(payload).encode()
sig = "sha256=" + hmac.new(b"livesecret", body, hashlib.sha256).hexdigest()
req = urllib.request.Request("http://127.0.0.1:3456/webhook", method="POST", data=body,
  headers={"Content-Type": "application/json", "X-GitHub-Delivery": "manual-1",
           "X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sig})
with urllib.request.urlopen(req) as r:
  print(json.load(r))   # expect {"ok": true, "matched": true, "results": [{"action": "start_review", ...}]}
PY
```

2. [ ] Expect a new `pr_review` task in the Tasks page, running with your reviewer agent (it will fail/`failed` because PR #999 doesn't exist — that's fine; the **assignment** goes `failed`, not stuck `running`).
3. [ ] The Triggers page Delivery log shows the delivery as `matched`.

### W-W2 — Dedup (re-delivery is a no-op)[Same, I'm not going to test this.]

1. [ ] Send the **exact same** command again (same `X-GitHub-Delivery: manual-1`).
2. [ ] Expect `{"ok": true, "deduplicated": true}` and **no** second reviewer task.

### W-W3 — Bad signature → 403[Same, I'm not going to test this.]

1. [ ] Send the same payload with a wrong signature:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:3456/webhook \
  -H "Content-Type: application/json" -H "X-GitHub-Delivery: manual-2" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: sha256=0000000000000000000000000000000000000000000000000000000000000000" \
  -d '{"action":"opened","repository":{"full_name":"example-account/example-test-repo"}}'
```

2. [ ] Expect `403`, and **no** delivery row / no task created.

### W-W4 — Unconnected repo → ignored[Same, I'm not going to test this.]

1. [ ] Send a delivery whose `repository.full_name` is not a connected repo (e.g. `other/repo`).
2. [ ] Expect `{"ok": true, "matched": false}` and a delivery row with status `ignored`.

### W-W5 — triage_issue masks payload secrets (working))

1. [X] Add a rule: event `issues.opened`, action `triage_issue`, instructions "fix it".
2. [X] Send an `issues.opened` delivery whose issue body contains a value matching a configured `secret_patterns` (or your actual token text).
3. [ ] Open the created `issue_fix` task → its stored context / the worktree `AGENTS.md` shows the value as `***` (masked), never raw.

### W-W6 — Replay (idempotent)

1. [X] In the Triggers page, click **replay** on the `manual-1` delivery. [Replay is idempotent for every action — no duplicate tasks/PRs, even for a `triage_issue`/`create_task` delivery.]
2. [X] Expect `matched: 1`, the replayed rule reported as `already dispatched — skipped`, and **no** new tasks/PRs. (A rule created after the original delivery still fires on replay.)

---

## 9. Settings live-vs-startup cheat sheet (new Phase-1 keys)

| Setting                             | Takes effect                                           |
| ----------------------------------- | ------------------------------------------------------ |
| `webhook_url`, `webhook_secret` | Immediately (read per delivery / at registration time) |
| All Phase-0 settings                | Unchanged (see`docs/12` §8)                         |

## 10. Known limitations (do NOT expect these yet)

- **Polling fallback** — the per-repo `poll_fallback` toggle exists on the repo model and the webhook status card, but **no poller is implemented**; webhooks are the only delivery path. Do not expect polling to start tasks.
- **Webhook delivery to a real tunnel** — only works if you expose Jalebi (`cloudflared`/`ngrok`) and set `webhook_url`; until then GitHub cannot reach the machine (that's what W-W1–W-W6 test locally instead).
- **Check runs / commit statuses** (PRD F15) — Phase 2; triggered tasks do not create GitHub check runs yet.
- **`start_review` requires a `pr_number` in the payload** — a `pull_request.opened` webhook without one (malformed) dispatches to no task (the rule matches, nothing is created).
- **Skills via `.claude/skills`** — opencode's native skills loading (`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` stays unset); if a future opencode version changes skills discovery, re-verify `docs/15` §3.

---

## Done checklist

When all boxes above are ticked, Phase 1 is validated. Note any failing check (page, step, expected-vs-actual) and share it — a failing check is a bug to fix, not a test failure.

**Quick automated gate first** (should be all green before manual QA):

```
cd apps/server && uv run pytest            # 416 passed
cd .. && npm test -w @jalebi/web           # 32 passed
npm run typecheck && npm run lint && npm run build
```
