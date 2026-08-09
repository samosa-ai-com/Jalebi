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
