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

| #   | What to expect                                                                                                                          | How to test                    | Pass |
| --- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ---- |
| 1.1 | **Settings → Agent backend** is now a dropdown with **opencode / codex / claude** (was opencode-only).                            | Settings page → "Agent backend". | ☐    |
| 1.2 | The backend dropdown is one line: picking it switches the agent for new tasks (the **Task**, **Task detail follow-up**, **Screenings**, and **Agents** model dropdowns now reflect the active backend). | Switch backends and open a task form. | ☐    |
| 1.3 | The **Repos / Screenings / Agents** pages show **Backend** selects that list **opencode / codex / claude** (plus the blank "default" option). | Open each page's create form.  | ☐    |
| 1.4 | No browser console errors on Settings, Tasks, Task detail, Screenings, or Agents.                                                       | DevTools → Console on each.    | ☐    |

---

## 2. Backend selection & model dropdown (PRD §F4/§F5)

| #   | What to expect                                                                                                                                                                                                                | How to test                                                                                                     | Pass |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- | ---- |
| 2.1 | Switching `agent.cli` to **codex** and opening a task form shows the **codex model list** in the Model dropdown (this machine: `gpt-5.4-mini`, `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-terra` — read from `~/.codex/models_cache.json`, `visibility: hide` models excluded). | Settings → Agent backend → codex; open the New Task form and open the Model dropdown.                           | ☐    |
| 2.2 | Switching `agent.cli` to **claude** shows the curated claude alias list (`default`, `sonnet`, `opus`, `haiku`, `sonnet[1m]`, `opus[1m]`, `best`, `fable`).                                                                      | Settings → Agent backend → claude; open the Model dropdown.                                                    | ☐    |
| 2.3 | Switching back to **opencode** restores the (long) opencode model list.                                                                                                                                                        | Settings → Agent backend → opencode.                                                                            | ☐    |
| 2.4 | `GET /api/models` returns `{"cli": "<active>", "models": [...]}` and never 500s for any of the three backends.                                                                                                                  | `curl -u jalebi:<pw> http://127.0.0.1:2052/api/models` with each backend.                                      | ☐    |
| 2.5 | **`adapter_model_lists` override:** setting it (e.g. `{"codex": ["my-model"]}` via `POST /api/settings`) makes `/api/models` return the override for that cli **before** the adapter's own list. An explicit empty list clears the dropdown. | `curl -X POST /api/settings -d '{"key":"adapter_model_lists","value":{"codex":["gpt-override"]}}'` → `/api/models`. | ☐    |

> **Note:** `adapter_model_lists` is a setting-only override (no Settings-page field). It exists so an owner with a custom provider can pin the exact models a dropdown offers.

---

## 3. Codex adapter — end-to-end run (PRD §F4)

> Uses the **cheapest model** (`gpt-5.4-mini`) to respect the free-account tier. All GitHub interaction for verification uses the PAT via `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"` — **never `gh`**.

| #   | What to expect                                                                                                                                                                                                                                                                                                | How to test                                                                                                                                                                                                   | Pass |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 3.1 | A task pinned to `cli=codex` runs as a codex child process and reaches **`done`**. The **timeline shows parsed events**, not raw JSONL: a `step` at the start (phase "step", carrying the codex `thread_id` as the session id), `message` lines, `tool_call` entries (command_execution / file_change), and a final `done`. | Create a `freeform` task on `example-repo`: `cli=codex`, `model=gpt-5.4-mini`, `publish_mode=manual`, prompt "create a file … and commit it; do not push". Watch the task detail timeline. | ☐    |
| 3.2 | **Notices are not fatal:** codex emits informational `error`-typed JSONL items (e.g. "Skill descriptions were shortened…"); the run **must not** be truncated — it completes and the notice appears as a timeline `message`.                                                                                       | In 3.1, confirm the run reached `done` despite the notice lines, and the notice text is visible as a message.                                     | ☐    |
| 3.3 | The run records `session_id` = the codex **`thread_id`** (UUID, e.g. `01a00571-…`), and `model` = `gpt-5.4-mini`.                                                                                                                                                                                              | `GET /api/tasks/<id>` → `run.session_id`, `run.model`.                                                                                          | ☐    |
| 3.4 | The agent's work lands in the worktree **without pushing** (manual publish).                                                                                                                                                                                                                                   | Confirm the created file exists in `~/.jalebi/ws/task-<id>/` and `git -C <wt> log --oneline -1` shows the agent's local commit; no branch pushed to `origin`. | ☐    |
| 3.5 | **Follow-up resume works:** posting a follow-up resumes the **same codex thread** (`codex exec resume <thread_id>`, run `seq` 2) and reaches `done`; the resumed agent has the prior conversation context.                                                                                                        | `POST /api/tasks/<id>/followup` `{"prompt": "read the file you created and reply with its exact contents"}` → run seq 2 done; the reply contains the file's contents. | ☐    |
| 3.6 | **Model on resume:** a follow-up that changes the model passes `-m` on `exec resume` (accepted by codex 0.147.0).                                                                                                                                                                                               | Optional: follow-up with a different codex model; confirm no error and the run completes.                                                         | ☐    |
| 3.7 | **Cancel/timeout path:** cancelling a running codex task kills the child (own process group) and finalizes as `cancelled`.                                                                                                                                                                                     | Start a task, cancel it, confirm status `cancelled` and no lingering `codex` process.                                                            | ☐    |

---

## 4. Per-CLI isolation guards (worktree bootstrap)

| #   | What to expect                                                                                                                                                                                                                                    | How to test                                                                                                                          | Pass |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ---- |
| 4.1 | A **codex** task worktree contains **`.codex/rules/default.rules`** with `prefix_rule(pattern=["gh"], decision="forbidden", …)`.                                                                                                                     | `ls ~/.jalebi/ws/task-<id>/.codex/rules/` + `cat` the file.                                                                          | ☐    |
| 4.2 | The codex guard denies `gh`: `codex execpolicy check --rules <wt>/.codex/rules/default.rules -- gh pr view 1` → `decision: forbidden`; `-- git status` → allowed (no matched rules).                                                                 | Run the check from a task worktree.                                                                                                  | ☐    |
| 4.3 | The guard files never appear in the agent's `git status`/commits (info/exclude + pre-commit hook); a `freeform` codex task's final diff contains **no** `.codex/`, `opencode.json`, or `CLAUDE.md` entries.                                           | `git -C <wt> status --porcelain` after a run; inspect the task's diff viewer.                                                        | ☐    |
| 4.4 | A **claude** task worktree contains **`.claude/settings.json`** with `permissions.deny` `Bash(gh*)` variants (and **`CLAUDE.md`** with the Jalebi rules block), plus `AGENTS.md`. (Claude reads `CLAUDE.md`, not `AGENTS.md`.)                       | Bootstrap is testable via the unit suite; for a live check, run any claude task (will fail on auth — see §8) and inspect the worktree files before cleanup. | ☐    |
| 4.5 | **repo-owned files are never clobbered:** if the test repo already tracks `.claude/settings.json` (or `.codex/` content), bootstrap preserves it and `remove_guard` (task delete) leaves it intact.                                                    | Covered by the automated suite (`test_worktree_bootstrap`); manual spot-check optional.                                              | ☐    |
| 4.6 | Deleting a task removes its guards (`.codex/`, `.claude/settings.json` only when Jalebi wrote it, marker blocks stripped from `AGENTS.md`/`CLAUDE.md`) and prunes the worktree.                                                                       | Delete a codex task; confirm `~/.jalebi/ws/task-<id>` is gone.                                                                        | ☐    |

---

## 5. Screening with the codex backend

| #   | What to expect                                                                                                                                                                                          | How to test                                                                                                | Pass |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ---- |
| 5.1 | A screen pinned to `cli=codex` runs its audit with codex; **Run now** completes and shows **findings** (or a clean "no findings"), with the same notices-not-fatal behavior as tasks.                     | Create a screen with Backend `codex` on `example-repo`, **Run now**, open History.                               | ☐    |
| 5.2 | The screening worktree got the **codex guard** (`.codex/rules/default.rules`) — the audit never uses `gh` and holds **no PAT** (the audit env is token-free).                                           | Inspect `~/.jalebi/ws/screen-<id>/` (or confirm via the unit suite); the findings step shows no credentials. | ☐    |
| 5.3 | Screenings with `cli=claude` are accepted at create time (validation passes); the run may fail at runtime here (no auth) — see §8.                                                                       | Create a screen with Backend `claude` → 201; Run now → clear failure, no 500.                              | ☐    |

---

## 6. Claude adapter (limited on this machine)

> **No Claude Code subscription / working proxy auth was available.** Live claude runs cannot be validated here; the adapter is **unit-tested with captured fixtures** (`test_claude_adapter`, 28 tests). What CAN be validated:

| #   | What to expect                                                                                                                                                                                               | How to test                                                                                               | Pass |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- | ---- |
| 6.1 | Selecting `cli=claude` (task or screen) is accepted; the model dropdown offers the curated claude aliases (§2.2).                                                                                             | Create a task/screen with `cli=claude`.                                                                   | ☐    |
| 6.2 | A claude run on this machine fails **cleanly** (no 500, no hang): the timeline shows an `error` event with **"Not logged in · Please run /login"** (the adapter maps claude's no-auth `result` line — `subtype:"success"` + `is_error:true`). | Run a claude task and read the timeline error.                                                            | ☐    |
| 6.3 | The claude adapter passes `--output-format stream-json --verbose --permission-mode bypassPermissions`; `ANTHROPIC_*` env is passed through for the owner's own claude auth.                                    | Covered by the unit suite; no live check possible here.                                                   | ☐    |
| 6.4 | **Deferred:** a real claude start + resume + tool-write smoke cannot be run on this machine (no subscription). Mark this as **N/A here** — it must be run on a machine with claude auth before merging.        | —                                                                                                         | N/A  |

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
