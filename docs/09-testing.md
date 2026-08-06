# 09 — Testing

> **Scope:** Test strategy per layer, how to run tests, and fixtures. Update this file for any test infrastructure changes.

---

## 1. How to run tests

- Tests: `npm test` (vitest).
- Lint/format: `npm run lint` / `npm run format`.
- Build: `npm run build`.

After any code change, run the relevant tests and build before marking work done. Existing tests take precedence: if a change breaks a test, fix the code, not the test (unless the user explicitly asks to change the test).

## 2. Test strategy per layer (PRD §13)

| Layer | Strategy |
|-------|----------|
| **Adapters** | Unit-tested with **mocked CLI output** (feed fixture JSON lines to `parse()`; assert normalized `AgentEvent`s). |
| **Queue** | Tested with **fake agents** (no real CLI) — assert concurrency, ordering, cancel, timeout, retry. |
| **Octokit interactions** | Mocked (e.g. via `nock`) — no real GitHub calls in unit tests. |
| **Screening scheduler** | Tested with **fake clocks** — assert cadence, baseline dedup, findings parsing. |
| **Webhook handler** | Tested with **fixture payloads** + delivery-id dedup — assert idempotency and rule matching. |
| **Git workspace mgr** | Tested against local throwaway git repos (init, worktree add/remove, branch naming). |
| **Secret masking** | Tested at the ingest layer — assert PAT/user patterns are redacted before broadcast/persist. |

## 3. Fixtures

- CLI output fixtures: captured `opencode --format json` event lines (and later codex/claude) for adapter tests.
- Webhook payload fixtures: `pull_request.opened`, `issues.opened`, `pull_request.synchronize`, etc.
- Fake agent: a script that emits a scripted sequence of events and exits — used by queue tests.

## 4. Testing against GitHub (integration)

- **All GitHub interaction for testing goes through `JALEBI_GITHUB_TOKEN`** (Octokit / `curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN"` / git with a credential helper) — **never `gh`** (PRD §17.2, AGENTS.md §3.3).
- The token is for a **dedicated testing repo/account** (not the Jalebi repo/account). Never point tests at production/user repos.
- The `gh` CLI is authorized **only** for local git operations on the Jalebi repo itself (staging, committing, pushing to `Rishabh-Bajpai/Jalebi`) — never for testing/verification against the testing account.

## 5. Reference

- PRD §13 (non-functional requirements — testability), §17.2 (no `gh` CLI), §17.3 (testing repo & account).