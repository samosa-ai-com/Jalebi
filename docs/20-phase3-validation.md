# 20 — Phase 3 Validation Checklist (manual QA)

> **Scope:** a step-by-step manual checklist for validating the Phase 3 features — **backend parity** (Codex + Claude Code adapters), **per-CLI isolation guards**, and the **per-task model dropdown** (`listModels()` per adapter) — through the UI at `http://127.0.0.1:2052`. It complements `docs/12-ui-validation.md` (Phase 0), `docs/17-phase1-validation.md` (Phase 1), and `docs/19-phase2-validation.md` (Phase 2) — run those first to confirm earlier phases still work, then this one for the new features. Expected behavior is given per check so you can tick things off as you go.

---

## 0. Prerequisites

- [X] **You are on the `phase-3` branch** (`git branch --show-current` → `phase-3`). Phase 3 was developed on a separate branch off the post-Phase-2 `main`. It is **not yet merged** — do not test on `main`.
- [X] Server is running on the Phase-3 code: `./stop.sh && ./start.sh`, then `http://127.0.0.1:2052/api/health` returns `{"status":"ok"}`. (`JALEBI_PORT` overrides the port.)
- [X] A GitHub PAT is bound to the test repo (GitHub page → account green). Recommended test repo: **`example-owner/example-repo`** (the designated test repo for QA).
- [X] The web UI is built: `npm run build` (the built `apps/web/dist` is what Flask serves).
- [X] **Local CLI availability (this machine):** `codex --version` → codex-cli 0.147.0 (authenticated, ChatGPT account); `claude --version` → 2.1.233 (**no Claude Code subscription available for validation** — claude runs cannot be live-validated here; see §8).

**Quick automated gate first** (should be all green before manual QA):

```
cd apps/server && uv run pytest            # 611 passed (as of the Phase-3 commits)
cd .. && npm test -w @jalebi/web           # 58 passed
npm run typecheck && npm run lint && npm run build
```

---

## 1. Shell & navigation

| #   | What to expect                                                                                                                                                                                                                 | How to test                           | Pass |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------- | ---- |
| 1.1 | **Settings → Default backend** is a dropdown (**opencode / codex / claude**) plus a required **Default model** (fed from `/api/models?cli=<default backend>`). These are the *fallbacks* — not a global override. | Settings page → "Default backend" + "Default model". | ☐    |
| 1.2 | The backend is chosen **per action**: the **New Task** form and the **Task detail follow-up** composer each have their own **Backend** select; Screenings/Agents already do. Each Model dropdown follows the Backend selected *in that form*. | Open a New Task form and a task's follow-up composer. | ☐    |
| 1.3 | The **Repos / Screenings / Agents** pages show **Backend** selects that list **opencode / codex / claude** (plus the blank "default" option).                                                                 | Open each page's create form.         | ☐   |
| 1.4 | No browser console errors on Settings, Tasks, Task detail, Screenings, or Agents.                                                                                                                                              | DevTools → Console on each.          | ☐   |
| 1.5 | The **New task** form caption shows the **backend selected in that form** (e.g. `codex · runs in a local worktree`); changing the Backend select updates it.                                                                                              | Open the New Task form; change the Backend select. | ☐   |

---

## 2. Backend selection & model dropdown (PRD §F4/§F5)

| #   | What to expect                                                                                                                                                                                                                                                                                     | How to test                                                                                                              | Pass |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ---- |
| 2.1 | Setting `default_backend` to **codex** makes the New Task form's Backend select default to **codex**, and its Model dropdown lists the **codex model list** (this machine: `gpt-5.4-mini`, `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-terra` — read from `~/.codex/models_cache.json`, `visibility: hide` excluded). | Settings → Default backend → codex; open the New Task form and open the Model dropdown. | ☐   |
| 2.2 | Setting `default_backend` to **claude** → the form's Backend select defaults to **claude** and the Model dropdown lists the curated claude aliases (`default`, `sonnet`, `opus`, `haiku`, `sonnet[1m]`, `opus[1m]`, `best`, `fable`). | Settings → Default backend → claude; open the New Task form. | ☐   |
| 2.3 | **Per-task backend:** in the New Task form, changing the **Backend** select (e.g. to codex while the default is opencode) refetches the Model dropdown for that backend, and the created task stores `cli` + `model` (runs on the picked backend regardless of the default). | New Task → Backend codex → Model list is codex's → Create → `GET /api/tasks/<id>` shows `cli: codex`. | ☐   |
| 2.4 | `GET /api/models` (no param) returns the `default_backend`'s list; `GET /api/models?cli=<backend>` returns that backend's list; unknown `?cli=foo` → empty, never a 500. | `curl -u jalebi:<pw> http://127.0.0.1:2052/api/models` and `?cli=codex`, `?cli=gemini`. | ☐    |
| 2.5 | **Default model:** Settings requires a **Default model** (empty is rejected). A task with no model on the default backend runs with `default_model`; a task pinned to a *different* backend ignores it (uses the CLI default). | Set `default_model`; create a task on the default backend (no model) → `run.model == default_model`; create on another backend → `run.model` null. | ☐    |
| 2.6 | **`adapter_model_lists` override:** `{"codex": ["my-model"]}` makes `/api/models` (and `?cli=codex`) return the override before the adapter's own list; an explicit empty list clears the dropdown. | `curl -X POST /api/settings -d '{"key":"adapter_model_lists","value":{"codex":["gpt-override"]}}'` → `/api/models?cli=codex`. | ☐    |
| 2.7 | **Unpinned screens follow `default_backend`:** a screen with **no** Backend pin runs on the default backend (a pinned screen keeps its own). Changing the default does **not** touch existing screens' pins. | Set `default_backend=codex`; run a screen with blank Backend → it runs on codex; a screen pinned to opencode still runs on opencode. | ☐    |
| 2.8 | **Follow-up backend override:** the follow-up composer has a Backend select (default = the task's backend). Changing it shows the note *"Changing the backend starts a fresh session with the previous conversation included."* and the follow-up starts a fresh run seeded with the prior conversation (same backend → normal resume). | In a done task's follow-up, change Backend → note appears → send → run `seq` increments, `run.session_id` differs from the original, the prompt contains the prior conversation. | ☐    |
| 2.9 | **Screenings "New task from finding"** spawns a `screen_finding` task that **inherits the screen's backend + model**; the **New Task** form no longer lists **Screen finding** as a manually creatable type. | In a screen's findings, "New task from finding" → task `cli` matches the screen; New Task → Task type has no "Screen finding". | ☐    |

> **Note:** `adapter_model_lists` is a setting-only override (no Settings-page field). It exists so an owner with a custom provider can pin the exact models a dropdown offers.

---

## 3. Codex adapter — end-to-end run (PRD §F4)

> Uses the **cheapest model** (`gpt-5.4-mini`) to respect the free-account tier. All GitHub interaction for verification uses the PAT via `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"` — **never `gh`**.

| #   | What to expect                                                                                                                                                                                                                                                                                                                                       | How to test                                                                                                                                                                                    | Pass |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 3.1 | A task pinned to`cli=codex` runs as a codex child process and reaches **`done`**. The **timeline shows parsed events**, not raw JSONL: a `step` at the start (phase "step", carrying the codex `thread_id` as the session id), `message` lines, `tool_call` entries (command_execution / file_change), and a final `done`. | Create a`freeform` task on `example-repo`: `cli=codex`, `model=gpt-5.4-mini`, `publish_mode=manual`, prompt "create a file … and commit it; do not push". Watch the task detail timeline. | ☐   |
| 3.2 | **Notices are not fatal:** codex emits informational `error`-typed JSONL items (e.g. "Skill descriptions were shortened…"); the run **must not** be truncated — it completes and the notice appears as a timeline `message`.                                                                                                       | In 3.1, confirm the run reached`done` despite the notice lines, and the notice text is visible as a message.                                                                                 | ☐   |
| 3.3 | The run records`session_id` = the codex **`thread_id`** (UUID, e.g. `01a00571-…`), and `model` = `gpt-5.4-mini`.                                                                                                                                                                                                                    | `GET /api/tasks/<id>` → `run.session_id`, `run.model`.                                                                                                                                  | ☐   |
| 3.4 | The agent's work lands in the worktree**without pushing** (manual publish).                                                                                                                                                                                                                                                                    | Confirm the created file exists in`~/.jalebi/ws/task-<id>/` and `git -C <wt> log --oneline -1` shows the agent's local commit; no branch pushed to `origin`.                             | ☐   |
| 3.5 | **Follow-up resume works:** posting a follow-up resumes the **same codex thread** (`codex exec resume <thread_id>`, run `seq` 2) and reaches `done`; the resumed agent has the prior conversation context.                                                                                                                         | `POST /api/tasks/<id>/followup` `{"prompt": "read the file you created and reply with its exact contents"}` → run seq 2 done; the reply contains the file's contents.                     | ☐   |
| 3.6 | **Model on resume:** a follow-up that changes the model passes `-m` on `exec resume` (accepted by codex 0.147.0).                                                                                                                                                                                                                          | Optional: follow-up with a different codex model; confirm no error and the run completes.                                                                                                      | ☐   |
| 3.7 | **Cancel/timeout path:** cancelling a running codex task kills the child (own process group) and finalizes as `cancelled`.                                                                                                                                                                                                                   | Start a task, cancel it, confirm status`cancelled` and no lingering `codex` process.                                                                                                       | ☐   |

---

## 4. Per-CLI isolation guards (worktree bootstrap)

| #   | What to expect                                                                                                                                                                                                                                                | How to test                                                                                                                                                   | Pass |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 4.1 | A**codex** task worktree contains **`.codex/rules/default.rules`** with `prefix_rule(pattern=["gh"], decision="forbidden", …)`.                                                                                                              | `ls ~/.jalebi/ws/task-<id>/.codex/rules/` + `cat` the file.                                                                                               | ☐   |
| 4.2 | The codex guard denies`gh`: `codex execpolicy check --rules <wt>/.codex/rules/default.rules -- gh pr view 1` → `decision: forbidden`; `-- git status` → allowed (no matched rules).                                                                 | Run the check from a task worktree.                                                                                                                           | ☐   |
| 4.3 | The guard files never appear in the agent's`git status`/commits (info/exclude + pre-commit hook); a `freeform` codex task's final diff contains **no** `.codex/`, `opencode.json`, or `CLAUDE.md` entries.                                    | `git -C <wt> status --porcelain` after a run; inspect the task's diff viewer.                                                                               | ☐   |
| 4.4 | A**claude** task worktree contains **`.claude/settings.json`** with `permissions.deny` `Bash(gh*)` variants (and **`CLAUDE.md`** with the Jalebi rules block), plus `AGENTS.md`. (Claude reads `CLAUDE.md`, not `AGENTS.md`.) | Bootstrap is testable via the unit suite; for a live check, run any claude task (will fail on auth — see §8) and inspect the worktree files before cleanup. | ☐   |
| 4.5 | **repo-owned files are never clobbered:** if the test repo already tracks `.claude/settings.json` (or `.codex/` content), bootstrap preserves it and `remove_guard` (task delete) leaves it intact.                                               | Covered by the automated suite (`test_worktree_bootstrap`); manual spot-check optional.                                                                     | ☐   |
| 4.6 | Deleting a task removes its guards (`.codex/`, `.claude/settings.json` only when Jalebi wrote it, marker blocks stripped from `AGENTS.md`/`CLAUDE.md`) and prunes the worktree.                                                                       | Delete a codex task; confirm`~/.jalebi/ws/task-<id>` is gone.                                                                                               | ☐   |

---

## 5. Screening with the codex backend

| #   | What to expect                                                                                                                                                                                     | How to test                                                                                                   | Pass |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ---- |
| 5.1 | A screen pinned to`cli=codex` runs its audit with codex; **Run now** completes and shows **findings** (or a clean "no findings"), with the same notices-not-fatal behavior as tasks. | Create a screen with Backend`codex` on `example-repo`, **Run now**, open History.                         | ☐   |
| 5.2 | The screening worktree got the**codex guard** (`.codex/rules/default.rules`) — the audit never uses `gh` and holds **no PAT** (the audit env is token-free).                      | Inspect`~/.jalebi/ws/screen-<id>/` (or confirm via the unit suite); the findings step shows no credentials. | ☐   |
| 5.3 | Screenings with`cli=claude` are accepted at create time (validation passes); the run may fail at runtime here (no auth) — see §8.                                                              | Create a screen with Backend`claude` → 201; Run now → clear failure, no 500.                              | ☐   |

---

## 6. Claude adapter (limited on this machine)

> **No Claude Code subscription / working proxy auth was available.** Live claude runs cannot be validated here; the adapter is **unit-tested with captured fixtures** (`test_claude_adapter`, 28 tests). What CAN be validated:

| #   | What to expect                                                                                                                                                                                                                                                     | How to test                                             | Pass |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------- | ---- |
| 6.1 | Selecting`cli=claude` (task or screen) is accepted; the model dropdown offers the curated claude aliases (§2.2).                                                                                                                                                | Create a task/screen with`cli=claude`.                | ☐   |
| 6.2 | A claude run on this machine fails**cleanly** (no 500, no hang): the timeline shows an `error` event with **"Not logged in · Please run /login"** (the adapter maps claude's no-auth `result` line — `subtype:"success"` + `is_error:true`). | Run a claude task and read the timeline error.          | ☐   |
| 6.3 | The claude adapter passes`--output-format stream-json --verbose --permission-mode bypassPermissions`; `ANTHROPIC_*` env is passed through for the owner's own claude auth.                                                                                     | Covered by the unit suite; no live check possible here. | ☐   |
| 6.4 | **Deferred:** a real claude start + resume + tool-write smoke cannot be run on this machine (no subscription). Mark this as **N/A here** — it must be run on a machine with claude auth before merging.                                               | —                                                      | N/A  |

---

## 7. Known limitations (do NOT expect these yet)

- **Claude live runs** cannot be validated here (no subscription available). The claude adapter is code-complete + unit-tested against captured fixtures; a real-auth smoke is the **only remaining manual gate** before merge.
- **codex sandbox mode on this machine is `danger-full-access`** — user namespaces are blocked (`bwrap` fails), so `workspace-write` cannot write here. On a userns-capable machine codex runs under `sandbox_mode=workspace-write` (network enabled via config override). This is an automatic per-machine probe, not a setting.
- **codex model list** comes from the per-account `~/.codex/models_cache.json`; arbitrary `-m` names (e.g. `gpt-4.1-mini`, `gpt-5-codex`) are rejected server-side for ChatGPT accounts — only the account's codex models work.
- **Claude disk confinement** is weaker than opencode/codex: there is no config-level "deny outside cwd" for claude (`additionalDirectories` is trust-gated). Claude worktree confinement = `CLAUDE.md`/`AGENTS.md` rules + no GitHub credentials. Documented tradeoff (owner machine trusted).
- **`ANTHROPIC_*` env pass-through** is deliberate (claude auth); it is not stripped by the agent env builder.
- **opencode `serve` shared backend** (per-run MCP cold-boot optimization) was intentionally deferred — not part of this phase.
- **codex `--output-schema` structured findings** were deferred — screening still parses findings from the agent output (works for all backends).

---

## Done checklist

- [ ] All of §1–§6 pass (or are explicitly marked N/A with a reason).
- [ ] §0 automated gate is green (backend 611 / web 58 / build).
- [ ] A codex task ran `done` with a parsed timeline (§3.1), a notice did not truncate it (§3.2), and a follow-up resumed the same thread (§3.5).
- [ ] The codex worktree guard denied `gh` (§4.2) and nothing guard-related leaked into the diff (§4.3).
- [ ] The claude checks in §6.1/§6.2 pass (selection accepted; clean no-auth failure) and §6.4 is marked N/A with the real-auth smoke noted as the merge gate.

**Any comment you write against a row above becomes the actionable feedback for the remediation pass** (like Phase 2's checklist did) — mark each row ☐Y / ☐N / comment, and the follow-up fixes will be planned against exactly those rows.
