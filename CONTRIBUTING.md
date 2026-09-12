# Contributing to Jalebi

Thanks for your interest in contributing to Jalebi! We welcome contributions from the community.

Jalebi drives coding agents that clone repos, push branches, open PRs, and post review comments using your GitHub PAT — so contributions carry a little more weight than usual. Please read the [Safety-sensitive changes](#safety-sensitive-changes) section before touching the queue, git workspace, GitHub client, or publish paths.

## Quick start

1. **Fork & clone**

   ```bash
   git clone https://github.com/samosa-ai-com/Jalebi.git
   cd Jalebi
   ```

2. **Install dependencies**

   ```bash
   uv sync --project apps/server   # server deps (run from repo root)
   npm install                     # web deps (repo root)
   ```

3. **Configure and run**

   ```bash
   cp -n .env.example .env   # then set JALEBI_PASSWORD in .env (required)
   npm run build              # build the web UI (served by Flask)
   ./start.sh                 # API + UI on http://127.0.0.1:2052
   ```

Full prerequisites (Python 3.11+, 3.13 recommended; Node/npm; `uv`; git; one or more agent CLIs: `opencode`, `codex`, `claude`), PAT scopes, and troubleshooting live in [`docs/00-overview.md`](docs/00-overview.md), [the docs site](https://samosa-ai.com/jalebi/docs), and [`AGENTS.md`](AGENTS.md).

## How to contribute

### 1. Find or open an issue

- **Always start with an issue.** Please don't open a PR without a corresponding issue.
- Check [existing issues](https://github.com/samosa-ai-com/Jalebi/issues) first to see if it's already being worked on.
- For anything non-trivial, describe the approach in the issue and wait for a maintainer to weigh in before writing code.

### 2. Branching strategy

- **`main`** — the **release** branch. Tagged builds are cut from here. **Do NOT submit PRs to `main`.**
- **`development`** — the **active development** branch. All features and fixes merge here first.

```bash
git checkout development
git pull origin development
git checkout -b feat/your-feature-name
```

Branch prefixes in use: `feat/`, `fix/`, `perf/`, `chore/`, `docs/`.

### 3. Make your changes

- **Agents start here:** [`AGENTS.md`](AGENTS.md) is the mandatory entry point. The authoritative behavior spec is [`JALEBI_PRD.md`](JALEBI_PRD.md) — if code, docs, or instinct disagree with the PRD, flag it instead of silently picking a side.
- Match the style of the surrounding code — naming, comment density, and idiom. Follow PRD Goal #10: simplicity above all, no over-engineering.
- Add tests for new behaviour. Server: pytest under `apps/server/tests/`; web: vitest under `apps/web/`.
- Every behavioral or architectural change must update the matching file under `docs/` in the same change.

### 4. Run the checks locally

These must pass on your machine before you open a PR:

```bash
uv run --project apps/server pytest        # server tests
uv run ruff check                          # server lint (run from repo root or apps/server)
npm run test -w @jalebi/web                # web tests
npm run lint                               # web lint
npm run build                              # production UI build
```

> Server notes: run pytest via `uv run --project apps/server pytest` from the repo root (or `uv run pytest` inside `apps/server`). No test may hang the suite — pytest enforces a per-test timeout.

### 5. Submit a pull request

- **Target the `development` branch.**
- Link the related issue (e.g. `Closes #123`).
- Describe what changed and how you verified it (tests, build, manual checks). Screenshots or a short recording help a lot for UI and timeline changes.

## Safety-sensitive changes

Extra scrutiny applies to anything that:

- touches the **GitHub client**, PAT vault (`secrets.py`), or token-authenticated push,
- changes the **task queue / worker pool**, run lifecycle (cancel/timeout/retry), or publish flow,
- widens what **screening** can do (screening is notify-only — it must never auto-act),
- changes **secret masking**, the `gh` CLI guard, webhook/trigger rules, or commit-status gating,
- touches credential storage or how data leaves the machine.

These need an explicit design discussion in the issue first. Changes that weaken a safety boundary will be declined without a strong justification. See [`docs/10-security.md`](docs/10-security.md) and [`docs/05-github-integration.md`](docs/05-github-integration.md) for the model you're working within.

## Code standards

- **Server:** Python 3.11+ (3.13 recommended), Flask + SQLAlchemy 2 + Alembic + httpx; `ruff` clean (line length 100).
- **Web:** React + Vite + Tailwind; ESLint + TypeScript clean; production build must pass.
- **Commits:** clear, descriptive messages. Conventional prefixes (`feat:`, `fix:`, `chore:`, `docs:`) are used throughout the history.
- **Secrets:** never commit `.env`, `.jalebi/`, `secrets.json`, PATs, or real endpoint values. Never use the `gh` CLI. Every PAT is an equal named account added via the GitHub page UI — there is no primary/default token and no fallback.

## Project structure

```text
Jalebi/
├── AGENTS.md                  ← mandatory agent entry point
├── JALEBI_PRD.md              ← authoritative behavior spec
├── README.md / CONTRIBUTING.md / SECURITY.md / LICENSE
├── docs/                      ← per-topic companion docs
├── start.sh / stop.sh         ← manage the server daemon
├── apps/
│   ├── server/                # Flask orchestrator (uv; `jalebi` console script)
│   │   ├── src/jalebi/        # app, config, db, github, git_workspace,
│   │   │                      # adapters/, queue, screening, routes/, ...
│   │   └── tests/             # pytest suite
│   └── web/                   # React + Vite + Tailwind (built → served by Flask)
│       └── src/pages/         # Tasks, TaskDetail, Screenings, ...
└── .env.example               ← committed placeholder (never commit real values)
```

## Help & documentation

- **Docs:** [samosa-ai.com/jalebi/docs](https://samosa-ai.com/jalebi/docs) — start at [`docs/00-overview.md`](docs/00-overview.md).
- **Get help:** [open an issue](https://github.com/samosa-ai-com/Jalebi/issues) describing what you're stuck on.
- **Security bugs:** don't open an issue — see [`SECURITY.md`](SECURITY.md).

## Using code from other projects

We value the spirit of open source, which fosters collaboration and code reuse. However, free software is not the same as the public domain, and license terms still apply. Not all code can be freely reused in every context.

Before integrating a significant portion of code — or adding new dependencies or libraries — please consult the maintainers to verify license compatibility. AGPL-3.0 compatibility is a hard requirement.

## License

By contributing, you agree that your contributions will be licensed under the [AGPL-3.0 License](LICENSE).

---

Thank you for helping make Jalebi better!
