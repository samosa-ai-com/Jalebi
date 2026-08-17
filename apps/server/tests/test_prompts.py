"""Tests for the prompt/AGENTS.md builders."""

from jalebi import catalog, prompts, tasks
from jalebi.db import Repo


def _task(
    session,
    repo,
    *,
    type_: str = "freeform",
    issues: list[int] | None = None,
    prs: list[int] | None = None,
    context: dict | None = None,
) -> tasks.Task:
    return tasks.create_task(
        session,
        type_=type_,
        repo_id=repo.id,
        prompt="do it",
        issues=issues,
        prs=prs,
        context=context,
    )


def test_agent_md_issue_fix_embeds_issue(session) -> None:
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    task = _task(
        session,
        repo,
        type_="issue_fix",
        issues=[1],
        context={
            "issues": [
                {"number": 1, "title": "Bug", "body": "It is broken", "html_url": "u"}
            ]
        },
    )
    md = prompts.build_agent_md(task, repo)
    assert "Never use the `gh` CLI" in md
    assert "#1 — Bug" in md
    assert "It is broken" in md
    assert "Closes" in md or "target branch" in md
    assert "BEGIN UNTRUSTED DATA" in md
    assert "END UNTRUSTED DATA" in md
    assert "prompt-injection" in md


def test_agent_md_pr_review_embeds_pr(session) -> None:
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    task = _task(
        session,
        repo,
        type_="pr_review",
        prs=[7],
        context={
            "prs": [
                {
                    "number": 7,
                    "title": "Feature",
                    "body": "Adds x",
                    "html_url": "u",
                    "base": "main",
                    "head": "feature/x",
                    "state": "open",
                    "author": "bob",
                    # The route now attaches review comments to PR context even
                    # for pr_review; the review block must ignore them (the
                    # "Linked pull request" section is for fixer tasks only).
                    "reviews": [{"author": "carol", "body": "needs tests"}],
                }
            ]
        },
    )
    md = prompts.build_agent_md(task, repo)
    assert "PR #7" in md
    assert "review.md" in md
    assert "Do **not** modify files or push anything" in md
    assert "BEGIN UNTRUSTED DATA" in md
    assert "Adds x" in md
    assert "Linked pull request" not in md
    assert "review comments to address" not in md


def test_agent_md_freeform_linked_pr_embeds_pr_and_reviews(session) -> None:
    """A freeform task that links a PR gets the PR + review comments in AGENTS.md."""
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    task = _task(
        session,
        repo,
        type_="freeform",
        prs=[7],
        context={
            "prs": [
                {
                    "number": 7,
                    "title": "Feature",
                    "body": "Adds x",
                    "html_url": "u",
                    "base": "main",
                    "head": "feature/x",
                    "state": "open",
                    "author": "bob",
                    "reviews": [
                        {"author": "carol", "body": "needs tests"},
                        {"author": "dave", "body": "secret-value-123 in a comment"},
                    ],
                }
            ]
        },
    )
    md = prompts.build_agent_md(task, repo)
    assert "## Linked pull request" in md
    assert "PR #7 — Feature" in md
    assert "Adds x" in md
    assert "## PR review comments to address" in md
    assert "### Review 1 — carol" in md
    assert "needs tests" in md
    assert "### Review 2 — dave" in md
    assert "BEGIN UNTRUSTED DATA: PR review comment" in md
    assert "pr.md" in md  # freeform still gets the PR-description note


def test_agent_md_freeform_linked_pr_fallback_mentions_pr_and_curl(session) -> None:
    """A task whose PR context was never fetched still names the linked PR and
    tells the agent how to fetch it (the token is in the agent env)."""
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    task = _task(session, repo, type_="freeform", prs=[3])
    md = prompts.build_agent_md(task, repo)
    assert "## Linked pull request" in md
    assert "PR #3" in md
    assert "owner/repo/pulls/3" in md
    assert "$JALEBI_GITHUB_TOKEN" in md


def test_followup_prompt_includes_constraints(session) -> None:
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    task = _task(session, repo)
    prompt = prompts.build_followup_prompt(task, repo, "address the reviewers")
    assert prompt.startswith("address the reviewers")
    assert "no gh" in prompt


def test_agent_md_merges_personality_and_skills(session) -> None:
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    agent = catalog.create_agent(
        session,
        id="security-auditor",
        name="Security Auditor",
        kind="reviewer",
        personality_md="You are a senior application security engineer.",
        skills=[
            {"name": "secure-coding", "content": "# Secure coding\n"},
            {"name": "owasp-top10", "content": "# OWASP\n"},
        ],
    )
    task = _task(session, repo)
    md = prompts.build_agent_md(task, repo, agent=agent)
    assert "## Agent personality" in md
    assert "You are a senior application security engineer." in md
    assert "## Skills" in md
    assert "@.claude/skills/secure-coding/SKILL.md" in md
    assert "@.claude/skills/owasp-top10/SKILL.md" in md
    assert "Agent: `Security Auditor` (security-auditor)" in md


def test_agent_md_lists_skills_for_codex(session) -> None:
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    agent = catalog.create_agent(
        session,
        id="security-auditor",
        name="Security Auditor",
        kind="reviewer",
        personality_md="You are a senior application security engineer.",
        skills=[
            {"name": "secure-coding", "content": "# Secure coding\n"},
            {"name": "owasp-top10", "content": "# OWASP\n"},
        ],
    )
    task = _task(session, repo)
    md = prompts.build_agent_md(task, repo, agent=agent, cli="codex")
    # codex does not resolve @path imports; it triggers skills by `$name` and
    # loads the body from .codex/skills/ (or .agents/skills/).
    assert "## Skills" in md
    assert "Use `$secure-coding`" in md
    assert "Use `$owasp-top10`" in md
    assert ".codex/skills/secure-coding/SKILL.md" in md
    assert "@.claude/skills/secure-coding/SKILL.md" not in md


def test_agent_md_without_agent_no_personality_section(session) -> None:
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    task = _task(session, repo)
    md = prompts.build_agent_md(task, repo, agent=None)
    assert "## Agent personality" not in md
    assert "## Skills" not in md


def test_agent_md_never_instructs_working_tree_restore(session) -> None:
    """Hard-rule #10 must only instruct UNSTAGING (git restore --staged), never
    a working-tree restore of AGENTS.md — a full restore deletes the Jalebi
    block and with it the task's linked-PR context (the exact failure that lost
    task #41's PR review comments)."""
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    task = _task(session, repo)
    md = prompts.build_agent_md(task, repo)
    assert "git restore --staged AGENTS.md" in md
    assert "git restore AGENTS.md" not in md  # full (working-tree) restore forbidden
    assert "git checkout" in md
    assert "reset --hard" in md
