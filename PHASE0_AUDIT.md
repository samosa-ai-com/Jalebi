# Jalebi — Phase 0 Comprehensive Review

> **Audit performed in read-only mode.** No files were modified. All `file_path:line_number` references were verified by direct read or by subagent trace. Test count: backend 206 (HANDOFF claims 207 — off by one, see T-1). Six parallel subagents audited security, queue/lifecycle, git/adapters, routes/API, web, tests, and PRD/doc compliance against the actual implementation.

---

## 0. Executive summary

Phase 0 is **functionally near-complete and well-tested**, with discipline around the threat model the owner cares about (token never leaves the server, masking at ingest, `gh` ban layered, localhost binding). No path was found that leaks the PAT online, no remote code execution, no remote unauthenticated access surface, no broken state machine that loses work, no runnable critical race. The 25-step HANDOFF remediation history shows a thorough audit chain, and the current state reflects it.

What was found:

- **6 decisions** the owner approved (will become implementation work below).
- **8 logic / correctness issues** (HIGH/MEDIUM) the report recommends fixing alongside the 6 decisions.
- **~25 doc/code drift items** (mostly MEDIUM/LOW) — `docs/02` schema table is stale in 5 places, `docs/12` task filter and Re-run descriptions are out of date, `docs/04` describes a `--mirror` flag the code doesn't use, `docs/05` describes webhooks/check-runs in present tense though they're Phase 1/2, `docs/10` advertises an unimplemented password, settings inventory misses `ntfy_url` + `agent_cli`, etc.
- **1 PRD gap the owner decided to fix**: diff viewer is a Phase 0 PRD item with no implementation.
- **1 PRD gap the owner decided to leave**: `screen_finding` task type exposed in Phase 0 form.
- **Several genuine test gaps** and **one test-count inconsistency**.

The "perfect Phase 0" target is achievable in ~17 changes: the 6 approved, 8 logical fixes recommended, and 1 test-count reconciliation. The doc drift is large but mechanical.

---

## 1. Decisions → proposed fix scope

Each item below pairs the question with the smallest safe change set. **This is the plan; nothing is executed.** When approved, implement in small, verified steps.

| # | Decision | Files touched | Verification |
|---|----------|---------------|--------------|
| D-1 | **Implement diff viewer** | `adapters/opencode.py` (emit a `diff` event when files change), `queue.py` (capture `git diff --numstat` at run end), `routes/tasks.py` (new `GET /api/tasks/<id>/diff`), `apps/web/src/pages/TaskDetail.tsx` (Diff tab + unified diff renderer using a tiny client-side renderer or a v1 view with line counts) | new test for diff snapshot + web test for Diff tab + lint/build |
| D-2 | **Implement `JALEBI_PASSWORD`** | `config.py` (read env), `app.py` (Basic-Auth decorator on `/api/*` + SPA when set) | new test for env-off (no gate), env-on (401 → browser shows prompt), `/api/health` exempted |
| D-3 | **Named-account context fetch** | `apps/web/src/pages/Tasks.tsx:124` → pass `repo.pat_name ?? undefined` | new web test |
| D-4 | **Action button errors** | `apps/web/src/pages/TaskDetail.tsx:517,520,523` → `.catch(e => setError(...))` + `busy` guard | new web test |
| D-5 | **`Closes #N` from `issues_json`** | `queue.py:880-883` → iterate `task.issues_json` instead of `re.findall(r"#(\d+)", task.prompt)`; mirror in `_comment_on_issues` (already correct) | update `test_publish_closes_linked_issues` (or equivalent) |
| D-6 | **`screen_finding` left as-is** | — (no change) | — |

---

## 2. Confirmed safe (no leak surface)

These are the security-relevant claims that were traced end-to-end and confirmed.

### 2.1 Token never leaves the server
- All `routes/github.py` endpoints return `TokenInfo(mask_only)` — `_mask_token` is `first 4 … last 4`. No raw token field exists in `asdict(TokenInfo)`. (`routes/github.py:51-70`, `routes/github.py:186-229`)
- `task_to_dict`, `run_to_dict`, `repo_to_dict` expose only `pat_name` (label) — no token values.
- Web UI never renders a token. `apps/web/src/pages/Tasks.tsx` shows masked previews only. No `token` field in any web type.

### 2.2 Token never in argv / process list
- All `subprocess` invocations authenticate via env: `git_workspace.py` `_auth_env` returns `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0` env pairs (base64-encoded `x-access-token:<PAT>`). Token never appears in `args`. (Verified by grep of every `subprocess` call site.)
- `queue.py:_build_agent_env` puts the token in the agent's env, not in argv. (`queue.py:37-56`)

### 2.3 Token never written into prompts / PRs / reviews
- `prompts.py` references only the literal `$JALEBI_GITHUB_TOKEN` substitution, never the value. (`prompts.py:78, 136`)
- `queue.py:_pr_title_and_body` applies `masker` to title + body before POST. (`queue.py:876-878`)
- `_run_review` applies `masker` to review.md content before `client.post_pr_review`. (`queue.py:509,513`)

### 2.4 Masking at ingest, before broadcast/persist
- Single choke point in `_stream_and_finish`: the same masked `entry` is used for both `publish` (SSE) and `steps.append` (DB). No publish-before-mask race. (`queue.py:291-292`)
- Masker is built from `secrets.all_token_values(config) + [token]` so it covers `.env` primary + every named PAT + the task's own token. (`queue.py:387`, `secrets.py:137-147`)
- Empty tokens skipped; tokens `re.escape`d.

### 2.5 `gh` CLI hard ban (three layers)
- Per-worktree `opencode.json` denies `gh*` variants. (`worktree_bootstrap.py:27-46`)
- Agent env strips `GH_TOKEN`/`GITHUB_TOKEN` and sets `GH_CONFIG_DIR=/nonexistent-jalebi-gh`. (`queue.py:44-56`)
- No `subprocess` anywhere in `apps/server/src` invokes `gh`. (verified by grep)
- Caveat (L1 below): bash-wrapped `gh` (`bash -c 'gh auth status'`) bypasses the deny rule. The env backstop catches it.

### 2.6 Localhost binding
- `config.py` default `host=127.0.0.1`. `app.run(host=config.host, ...)`. Not `0.0.0.0`.
- Flask adds no CORS headers → no remote-origin API calls. Confirmed by grep.

### 2.7 Secrets file perms + non-leakage
- `secrets.json` written `0600` (atomic write via tmp + chmod + rename). Test asserts perms. (`secrets.py:26-51`)
- No stack/log line prints the token. Confirmed by grep of every `print`/`log`/`except` block.

### 2.8 No `eval`/`exec`/`pickle` on user-supplied config
- `settings.py` uses `json.loads`/`json.dumps` only on DB-stored bytes; validators reject bad types. (Grep empty result set for `eval`/`exec`/`pickle`.)

### 2.9 No global 500 handler echoes request/env
- No `@app.errorhandler` registered; Flask's generic 500 yields no body/env echo.

---

## 3. Security findings (HIGH / MEDIUM)

### H1. Artifact content is served without masking
**`routes/tasks.py:322-361` (`artifact_content`, `download_artifact`)** serve captured worktree files with `send_file(...)`. The capture path in `artifacts.py:43-58` copies all untracked, non-ignored worktree files. `.jalebi/` is excluded via gitignore, but **no secret screening** is applied.

The agent subprocess has the token in its env and is coached to run `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"` for diagnostic PR comments. If the agent dumps a curl response (or the env) into a scratch file, that file is captured and the UI serves it unmasked. This is exactly the masking gap PRD §F17 is meant to prevent.

**Why it matters:** this is the only real ingest→store→serve path where the masking guarantee breaks.
**Fix:** apply `masker` to artifact content at write time in `capture_run_artifacts`, or screen captured files against `all_token_values()`.

### M2. Unbounded masking regex substitution
**`masking.py:24-27` + `queue.py:780-787`:** `masker` is applied to the **full** agent tool output (which can be many MB) before truncation to `MAX_STEP_TEXT`. An owner-configured pathological `secret_patterns` regex (e.g. `(a+)+$`) yields catastrophic backtracking / server-thread hang.

**Fix:** cap `text` length before masking, and bound patterns at submission (regex compile-time check).

### M3. Masking gap on the `Closes` derivation (covered separately in D-5)
The current `re.findall(r"#(\d+)", task.prompt)` is not a leak path but is a correctness inconsistency between two mechanisms: the PR body's `Closes #N` comes from the prompt regex, while `_comment_on_issues` (`queue.py:839-851`) uses `task.issues_json`. A user prompt with an unrelated `#3` wrongly adds `Closes #3` to the PR. **Approved: derive from `issues_json`** (D-5).

### L1. `bash -c` argv on `opencode` spawn (fragility, not leak)
**`adapters/opencode.py:38-40`:** the entire CLI command — `--model`, `--session`, and the **prompt** — lives in the bash process `cmdline`. Currently safe because prompts are masked / never contain the token. But any future call that passes an un-redacted string into `start()` / `resume()` would persist it in argv (`ps`/`/proc/<pid>/cmdline`).

**Fix:** add a comment enforcing "all `args` must already be masked"; consider `--prompt`-via-stdin if the CLI supports it. Not a leak today.

### L2. `gh` deny rule can be bypassed by shell wrapping
**`worktree_bootstrap.py:27-46`:** `bash -c 'gh auth status'` won't match the deny rule. Acceptable because the env backstop strips `GH_TOKEN`/`GITHUB_TOKEN` regardless. Treat the opencode.json deny as documentation, not enforcement.

---

## 4. Logic / correctness findings (HIGH / MEDIUM)

### L-LOGIC-1. Soft-deleted repo is still runnable
**`routes/tasks.py:144` + `tasks.py:32-34`:** task creation fetches the repo by id without checking `repo.connected`. A repo the user explicitly disconnected can still receive new tasks — they go through `ensure_mirror` → worktree → agent → push/PR. The disconnect becomes cosmetic.

**Fix:** in `tasks.create_task`, raise `ValueError("repo is disconnected")` when `not repo.connected`.

### L-LOGIC-2. Per-task bootstrap files (`opencode.json`, `AGENTS.md`, modified `.gitignore`) can be committed into PRs
**`worktree_bootstrap.py:92-160`:** `write_agent_md` clobbers a repo's own `AGENTS.md` unconditionally; `write_opencode_guard` adds `opencode.json`; `write_gitignore` mutates the tracked `.gitignore`. The pre-commit hook only rejects `.jalebi/`. A normal `git add .` will sweep all three into the commit and push them into the PR diff, plus the repo's own `AGENTS.md` is lost.

**Fix:** extend `PRECOMMIT_HOOK` to also reject `opencode.json`, the rewritten `AGENTS.md`, and the `.gitignore` delta (mirror the `.jalebi/` rejection pattern).

### L-LOGIC-3. Cascade-delete skips soft-disconnected repos
**`routes/github.py:248-251`:** `DELETE /api/github/tokens/<name>` filters `repos` on `Repo.pat_name == name, Repo.connected.is_(True)`. A repo owned by the deleted account that was previously soft-disconnected is **not** removed; tasks on it are not deleted either. The account teardown then leaves orphaned tasks that, when run, silently fall back to the primary token.

**Fix:** drop the `connected.is_(True)` filter — cascade should cover every repo row with `pat_name == name`.

### L-LOGIC-4. `prune` aborts on first non-404 GitHub error
**`routes/repos.py:114-134`:** a single transient error on **one** repo raises and the entire sweep is lost. The `removed` list accumulated so far is discarded and nothing is committed.

**Fix:** move the `try/except` inside the per-repo loop, log+continue.

### L-LOGIC-5. Manual `rerun` silently disables future auto-retry
**`routes/tasks.py:246` + `queue.py:743`:** manual rerun bumps `retry_count`; the auto-retry guard `if (retry_count or 0) >= MAX_AUTO_RETRIES (1)` then prevents any future auto-retry.

**Fix:** track manual vs. automatic retries separately.

### L-LOGIC-6. `push_branch` bypasses the per-repo lock
**`git_workspace.py:295-299`:** every other mirror mutation goes through `with self._lock_for(full_name)`, but `push_branch` doesn't. With concurrency-4 workers on one repo, a push can race a fetch/worktree-add. Git's own ref-locks serialize writes (so no corruption), but the loser surfaces as an error.

**Fix:** wrap the push in the same lock.

### L-LOGIC-7. `resume()` does not forward `--model`
**`adapters/opencode.py:73-95` + `queue.py:616,642-648`:** PRD F4 quirk says "resuming keeps the session's original model unless `--model` is passed on resume (supported)." The adapter's `resume(cwd, session_id, prompt, env)` has no `model` parameter. UI's per-follow-up model override is accepted but has no effect.

**Fix:** add `model` to `resume()` and append `--model <m>` when set.

### L-LOGIC-8. `/api/repos/<id>/branches` reads stale mirror; GitHub listings have no pagination
**`routes/repos.py:138-151` + `github.py:231-238`:** the route calls `git.list_branches` (reads `refs/remotes/origin/*`) without `ensure_mirror`/fetch — newly-connected repo returns `[]` (or stale). And `GitHubClient.list_branches` issues a single `per_page=100` with no `while next` loop — anything past 100 is silently dropped. (Also affects `list_issues`, `list_prs`, `find_pr_by_head`.)

**Fix:** call `ensure_mirror` before reading; paginate with `Link` header.

---

## 5. Frontend findings (HIGH / MEDIUM)

### F1. Task form fetches GitHub context with primary token (HIGH)
**`apps/web/src/pages/Tasks.tsx:124`:** `api.getGithubContext(repo.full_name)` → backend uses primary token. For repos owned by a **named** account the context fetch fails (`.catch(() => {})` swallows it), the picker block `{(type === "issue_fix" || type === "pr_review") && context && …}` (`Tasks.tsx:238`) never renders, and the form hard-fails at submit ("Pick the issue to fix." / "Pick the pull request to review."). So you **cannot create structured tasks on any repo owned by a non-default account** — a headline supported flow.

**Fix:** pass `repo.pat_name ?? undefined`. **(D-3)**

### F2. Action buttons swallow errors (HIGH)
**`apps/web/src/pages/TaskDetail.tsx:517,520,523`:** Cancel/Re-run/Publish have no `.catch` and no `busy` guard. Double-click Re-run → second request 409s; publish failure (502 with real error string) is invisible.

**Fix:** add `.catch(e => setError(e.message))` + `busy` flag on each action. **(D-4)**

### F3. Hard-coded default model bypasses the "default model" option
**`apps/web/src/pages/Tasks.tsx:46,90`:** `DEFAULT_MODEL = "opencode-go/deepseek-v4-flash"` is set as initial state, so the `<option value="">default model</option>` is never used; every task sends that exact model string to the backend. A user whose opencode doesn't list it submits an invalid model.

**Fix:** seed with `""` until `/api/models` resolves.

### F4. SSE has no `seq` / no backfill
**`api/client.ts:113-136` + `routes/tasks.py:364-400`:** events have no `seq`, no `?after_seq=` backfill. On reconnect the server subscribes a fresh `queue.Queue` (`events.py:15-23`), so events emitted between old-subscriber-tear-down and new-subscriber-subscribe are **permanently dropped from live timeline**. The persisted `runs.steps_json` (-500) heals it on reload, but live-order fidelity is lost.

**Fix:** stamp `seq` in `_step_from_event`; support `?after_seq=` replay; frontend tracks last `seq` and dedupes.

### F5. `ntfy_url` accepted with no URL validation
**`Settings.tsx:179-190` + `app.py:28`:** backend validator is `lambda v: isinstance(v, str)` — `"htp:/ntfy.sh"` saves fine.

**Fix:** `new URL(value)` on frontend; stricter backend validator.

### F6. No client-side catch-all route → blank page on unknown paths
**`App.tsx:112-121`:** no `<Route path="*" …>`. Navigating to an unknown URL renders header + blank body.

**Fix:** add a `path="*"` route rendering a small 404 block.

### F7. Live timeline buffer is unbounded; event list keyed by index
**`TaskDetail.tsx:373,585,598`:** every live event is appended to `live` with no cap (backend persists only last 500); `TimelineItem` uses `key={i}` index keys.

**Fix:** cap live buffer to e.g. last N; use stable keys.

### F8. `load()` closes over stale `runs`/`selectedRunId`
**`TaskDetail.tsx:327-359`:** `load` is `useCallback(…, [taskId])` but reads `selectedRunId` and `runs` from the first-render closure. Manual older-run selection gets reset on the next run change.

**Fix:** use refs or functional updates.

### F9. Follow-up composer has no `if (busy) return;` guard
**`TaskDetail.tsx:147-164`:** the button is `disabled={busy}` so a typical double-click is protected, but two Enter-key presses within a single frame can both pass. Backend would happily enqueue two follow-ups.

**Fix:** `if (busy) return;` at the top of `submit`.

### F10. `getRuns` failure swallowed
**`TaskDetail.tsx:354`:** `.catch(() => {})` — runs endpoint silently disappears with no hint.

### F11. `Github.tsx` Prune "no deleted repos" styled as error
**`Github.tsx:229`:** `setError("No deleted repos found…")` on a successful no-op — renders in red as if broken.

### F12. Artifact preview lacks focus trap / Esc handling
**`TaskDetail.tsx:246-258`:** no `role="dialog"`/focus trap/Esc; closing mid-fetch triggers a state update on unmounted.

### F13. Settings concurrency `min/max 0/16` vs backend `0..64`
Frontend min/max is tighter than backend (cosmetic).

### F14. `getGithubStatus`, `getBranches` dead code
Not referenced anywhere in `apps/web/src` (`client.ts:52, 104-105`).

---

## 6. Test-suite findings (covering HANDOFF claims + gaps)

### T-1. Test count off by one
HANDOFF line 9 says "207 backend tests"; `pytest --collect-only` reports **206**. Reconcile (likely a renamed/merged test).

### MISSING TESTS

| # | What HANDOFF claims | What's untested |
|---|---------------------|-----------------|
| T-2 | `create_review_worktree` at `refs/pull/N/head`, detached HEAD | monkeypatched away in all pr_review tests; no real-git test exists |
| T-3 | `_comment_on_issues` fires on issue_fix publish | only the client method is tested; not the queue-level integration |
| T-4 | opencode `bash -c` spawn quirk (Step 12) | `_spawn` is monkeypatched everywhere; the `/bin/bash -c 'cd … && exec opencode …'` construction has no test |
| T-5 | Reconnect uses the repo's bound account | only default-account repo tested |
| T-6 | `"" → None` normalization for `connect_repo` | only the non-empty and default paths are tested |
| T-7 | Re-run on `interrupted` runs | only `cancelled` re-run is tested |
| T-8 | `default_timeout_minutes` honored in the watchdog | only route storage is tested, not the watchdog picking up the override |
| T-9 | Slow-but-not-silent stall stream | only total silence tested |
| T-10 | Multiple chained sequential follow-ups | only single follow-up tested |
| T-11 | Disconnected-repo task detail still shows `repo_full_name` | never asserted |

### WEAK TESTS

| # | Issue |
|---|-------|
| T-12 | `test_pr_review_followup_resumes_in_review_worktree` asserts only that the mock returns the review-worktree path; if the queue regressed to calling `create_worktree` instead of `create_review_worktree`, the test would still pass |
| T-13 | Cascade-delete tests don't verify "running/queued tasks cancelled first" before row deletion |

### BRITTLE TESTS

| # | Issue |
|---|-------|
| T-14 | `test_mirror_clone_auth_env`/`test_push_auth_env` assert exact `GIT_CONFIG_VALUE_0` key + base64 format — couples the test to implementation detail. The behavioral claim is "token never in argv"; assert that instead |
| T-15 | `test_success_marks_done` asserts `step_types == ["message","done"]` — if a future log inserts a `step` event the test breaks even though nothing behavioral changed |

### FLAKY TESTS

| # | Issue |
|---|-------|
| T-16 | `test_sse_streams_live_events_and_closes` uses `reader.join(timeout=5)` — non-deterministic on slow CI |
| T-17 | `test_cancel_running_task` polls and joins with timeout |
| T-18 | `test_stall_marks_failed_with_diagnostic` mutates module global `STALL_TIMEOUT_SECONDS` — low risk (serial pytest) but visible |

### OK (verified & correct)
- Stall guard (`test_stall_marks_failed_with_diagnostic`), pr_review resume-in-review-worktree, `--dir` on resume, PAT cascade delete (incl. different-PAT task), publish uses task's own account, `"default"` rejected, PR body/review masking, reconnect 404, issue_get_rejects_pr, pr_review posts COMMENT, find_pr_by_head dedup, multi-PAT, soft disconnect, live concurrency, restart recovery, cancel-race, secret_patterns masking, tool_call events persisted, seq via max+1, artifact capture/prune/traversal/excludes-jalebi, follow-ups (tagged tuples, resume, latest_resumable_run, seq+1, Followup row), publish idempotence/reuse, schema migrations match models, hermetic tests (no real network/opencode), web tests cover Tasks list/create/filter, TaskDetail streaming/follow-up/artifacts/cancel/re-run/polling, Github account mgmt & connect, Settings toggle.

---

## 7. PRD compliance & doc drift

### PRD gaps

| # | Gap | Decision |
|---|-----|----------|
| **G-1** | **Diff viewer is a Phase 0 PRD deliverable (`PRD §12`).** No diff rendering anywhere; `EVENT_TYPES` reserves a `diff` event but no adapter emits it. `docs/08-ui.md:62` and `docs/12-ui-validation.md:204` admit the gap. | **Implement now (D-1)** |
| G-2 | `screen_finding` task type exposed in Phase 0 form (per `docs/06` "reserved Phase 2/1"). Backend treats it as generic freeform. | **Leave as-is (D-6)** |

### Doc drift (D)

| # | Doc | Code | Fix |
|---|-----|------|-----|
| D-1 | `docs/02-data-model.md` schema | 5 columns missing from doc tables: `repos.pat_name`, `repos.connected`, `tasks.pat_name/issues_json/prs_json/context_json`, `runs.pat_name/pid`, `followups.pat_name/model` | re-sync §2 tables to current migrations |
| D-2 | `docs/02:9` says `jalebi.db` | `data.db` everywhere else | one-word fix |
| D-3 | `docs/02` settings list has 7 keys | settings has 9 — missing `ntfy_url`, `agent_cli` (also missing in `docs/08`, `docs/11`) | add the two |
| D-4 | `docs/04:49` says `-c remote.origin.mirror=false` for push | `git_workspace.py` uses `--bare` not `--mirror`, push is just `git push origin <branch>` | delete stale sentence |
| D-5 | `docs/05:53-74` describes webhooks & check-runs in present tense | no webhook/check-run code in Phase 0 | label as Phase 1/2 — not implemented |
| D-6 | `docs/12:79` says cancelled under Failed filter | `Tasks.tsx:356` Failed = `failed \|\| timed_out \|\| interrupted` | update doc or add `cancelled` |
| D-7 | `docs/12:229` says Re-run "**not** cancelled" | `TaskDetail.tsx:518-519` includes cancelled; HANDOFF Step 16 built it | update doc |
| D-8 | `docs/12:509` lists branch selectors + model dropdown as "later phases" | both implemented (`Tasks.tsx:89/161-162/302`) | remove from known-limitations |
| D-9 | `docs/09:20-21` test inventory missing 5 backend + 2 web test files | all exist | append file names |
| D-10 | `docs/10:9-11` + `.env.example` advertise `JALEBI_PASSWORD` | no code reads it | **(D-2)** — implement, or mark "planned" |
| D-11 | `docs/03` has duplicate `## 5` section; `docs/08` has duplicate `## 5` and `## 6` | cosmetic | renumber |
| D-12 | "Phase 0 complete" in HANDOFF line 9 | missing diff viewer (G-1) | resolved by D-1 |

### HANDOFF claims verified ✅
- `default_timeout_minutes` honored — `queue.py:360-364,717-761`. ✅
- `"default"` account name rejected — `routes/github.py:207-208` + `secrets.py:74-75`. ✅
- per-worktree `.gitignore` excludes `.jalebi/` — `worktree_bootstrap.py:79,125-173`. ✅
- pr_review follow-ups resume in **review worktree** — `queue.py:645-654`. ✅
- stall guard `STALL_TIMEOUT_SECONDS = 120` — `queue.py:31,772-785`. ✅

### Naming inconsistencies

| # | Issue |
|---|-------|
| N-1 | `JALEBI_PASSWORD` documented in 3 places, implemented in 0 → resolved by D-2 |
| N-2 | DB file name: `data.db` (code) vs `jalebi.db` (docs/02 line 9) → D-2 above |
| N-3 | `agent.cli` (PRD §F4 + docs/03) vs `agent_cli` (settings.py) — dotted vs underscore |

---

## 8. Implementation order (when approved)

Implement in this order, each step verified with tests/lint/build before the next.

1. **Decisions (smallest first):**
   - D-5 → `Closes #N` from `issues_json` (smallest, unblocks the masking-analysis consistency)
   - D-3 → named-account context (frontend)
   - D-4 → action button errors (frontend)
   - D-1 → diff viewer (PRD gap)
   - D-2 → `JALEBI_PASSWORD` (auth gate)

2. **Logical HIGH/MEDIUM fixes (recommended alongside):**
   - L-LOGIC-1 (soft-deleted repo still runnable)
   - L-LOGIC-3 (cascade delete skips disconnected)
   - L-LOGIC-2 (bootstrap files can be committed into PRs)
   - L-LOGIC-4 (prune abort on first error)
   - L-LOGIC-5 (manual rerun breaks auto-retry)
   - L-LOGIC-6 (push_branch lock)
   - L-LOGIC-7 (resume model)
   - L-LOGIC-8 (branches stale mirror + pagination)

3. **Security MEDIUMs:**
   - H1 (artifact content masking) — most important
   - M2 (unbounded masking regex) — cap text length + pattern validation

4. **Frontend MEDIUMs (smaller):**
   - F3 hardcoded model → F5 ntfy validation → F6 404 route → F7 bounded live buffer → F8 stale closure → F9 busy guard → F10..F14 polish

5. **Test gaps (T-1..T-18):** add the missing tests alongside each fix they verify.

6. **Doc drift (D-1..D-12):** mechanical batch at the end so a single PR closes the loop.

7. **Update HANDOFF.md** with the final list and bump the "Last completed work" entry.

---

## 9. Net assessment for "perfect Phase 0"

After this audit:

- **Online leakage risk: zero** (verified end-to-end).
- **Local leakage risk: low** (the artifact-content gap H1 is the only real one; the `JALEBI_PASSWORD` gap is mitigated by localhost binding).
- **Logical mismatches: ~8** (L-LOGIC-1 through L-LOGIC-8 + D-5 + F1 + F2), all with smallest-safe fixes.
- **Doc drift: large but mechanical** (12 items).
- **Test gaps: 11 missing + 4 brittle/flaky** (T-1..T-18).
- **PRD compliance: 1 gap** (diff viewer, D-1).

Implementing the 6 decisions + the 8 logical fixes + H1 + D-doc-drift fixes would close every HIGH and most MEDIUM in this report and leave a Phase 0 that is genuinely "perfect" by the stated criteria.

---

**This was a read-only review. No files were modified. The next step is to approve the implementation plan above (or a subset) and execute in small, verified steps per AGENTS.md §2.1.**
