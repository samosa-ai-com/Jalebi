# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| latest release | ✅ |
| older releases | ❌ |

Jalebi runs from source via `./start.sh` (see [README](README.md)) and ships tagged releases on [GitHub Releases](https://github.com/samosa-ai-com/Jalebi/releases). Fixes land in the next release — please update before reporting a bug against an older build.

## Reporting a vulnerability

We take security seriously. If you discover a security vulnerability in Jalebi, please report it responsibly.

### How to report

1. **DO NOT** create a public GitHub issue for security vulnerabilities.
2. Use [GitHub's private vulnerability reporting](https://github.com/samosa-ai-com/Jalebi/security/advisories/new), or email the maintainers at [contact@samosa-ai.com](mailto:contact@samosa-ai.com).

### What to include

- Description of the vulnerability
- Steps to reproduce — Jalebi version, backend/CLI + model, task type, and OS if relevant
- Potential impact
- Suggested fix (if any)

### Response timeline

- **Acknowledgment**: within 48 hours
- **Initial assessment**: within 7 days
- **Resolution**: depends on severity, typically within 30 days

### What to expect

- We will acknowledge your report promptly.
- We will keep you informed of our progress.
- We will credit you in the fix announcement, unless you prefer anonymity.

## Security considerations

Jalebi drives coding agents with push/PR/comment power on your GitHub account, so its threat model centers on credentials, local isolation, and publish guards. The areas below are where we most want your eyes.

### Localhost by design

- **Everything runs on your own machine** — Flask serves the API + built UI on one port (default `2052`); agents run as local child processes in per-task git worktrees.
- **GitHub PATs** live in a `0600` named vault (`<data-dir>/secrets.json`, added via the GitHub page UI) — never synced, never logged. `JALEBI_GITHUB_TOKEN` is masking-only.
- **Chats, run logs, and caches stay local** under your data dir (`~/.jalebi` by default).
- **What does leave the machine**: GitHub API calls made with your PAT (repos, issues, PRs, reviews, commit statuses, webhook registrations), and whatever your chosen agent backend sends to its model provider. Choose your provider accordingly, or run a local model.

### Safety boundaries

These are the controls a vulnerability report would most usefully target:

| Boundary | What it enforces |
|---|---|
| **UI auth** | Every route except `/api/health` requires HTTP Basic auth when `JALEBI_PASSWORD` is set; the server refuses to start when it is empty. |
| **PAT vault + masking** | All PATs are equal named accounts; no primary/default, no fallback. PAT-shaped values are redacted at ingest so they never survive into logs. |
| **`gh` CLI ban** | Hard-blocked three ways: per-worktree `opencode.json` permission rules, a stripped agent environment, and prompt instructions. Agents must never improvise credentials. |
| **Worktree isolation** | One worktree per task/agent; bare mirrors + review worktrees keep runs from trampling each other or your checkout. |
| **Screening is notify-only** | Scheduled audits can notify (ntfy) and open findings — they must never auto-act on your repos. A screening escape into action is a security bug. |
| **Publish guards** | Push + PR open/update (`Closes #N`) only through the reviewed publish path; commit statuses gate merges via branch protection. |
| **Webhook triggers** | Deliveries are deduped and rule-gated; registration uses your PAT. An unauthenticated trigger that starts work is a security bug. |
| **Env-var blocklist** | Agent-injected env vars pass a blocklist; secrets are masked before they reach prompts or the console. |

**Prompt injection is in scope.** Jalebi ingests issue/PR bodies, comments, webhook payloads, and diffs — all of which can carry attacker-controlled text. A report showing that injected content can drive the agent past a confirmation, publish guard, mode restriction, or credential boundary is exactly the kind of finding we want.

### Best practices for users

1. **Set a strong `JALEBI_PASSWORD`** and never expose your port beyond `127.0.0.1` unless you understand the consequences.
2. **Grant PATs least scope** — classic `repo`, or fine-grained Contents / Pull requests / Issues / Metadata / Commit statuses / Actions (read).
3. **Evaluate on a scratch repo** before pointing Jalebi at anything you care about.
4. **Review every diff before publishing.** Agents can hallucinate or push unexpected changes.
5. **Choose your model provider deliberately** — task content goes wherever your backend points. A local model server keeps everything on your network.
6. **Install only from official sources** — [GitHub Releases](https://github.com/samosa-ai-com/Jalebi/releases) or this repo. Verify tags and signatures.
7. **Keep backups.** An LLM driving git can get things wrong.

## Disclaimer

Jalebi is powered by coding agents operating across your repositories with push, PR, and review powers. Models can hallucinate, misinterpret instructions, or execute unexpected git/GitHub operations, which can cause accidental data loss, unexpected configuration changes, or unwanted public actions. We explicitly assume no responsibility or liability for any loss, damage, system issues, or data corruption incurred through use of Jalebi. Use it responsibly and at your own risk.

## Scope

This policy covers the Jalebi application in this repo. Third-party dependencies, agent backends/CLI tools, LLM providers, and GitHub itself have their own security policies and disclosure processes.

---

Thank you for helping keep Jalebi secure!
