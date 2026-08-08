"""Tests for the reviewer-workflow service (PRD F7)."""

import pytest

from jalebi import catalog, reviews
from jalebi.db import Repo
from jalebi.reviews import ReviewError


def _repo(session, pat_name: str = "test") -> Repo:
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name=pat_name,
    )
    session.add(repo)
    session.commit()
    return repo


def _reviewer_agent(session, slug: str = "security-auditor") -> None:
    catalog.create_agent(
        session,
        id=slug,
        name="Security Auditor",
        kind="reviewer",
        personality_md="Be adversarial.",
        skills=[{"name": "secure-coding", "content": "# x\n"}],
        custom_instructions="Review auth, secrets, injection.",
        enabled=True,
    )


def test_assign_reviewers_creates_one_task_per_reviewer(session) -> None:
    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")
    _reviewer_agent(session, "auditor-b")
    created = reviews.assign_reviewers(session, repo, 42, ["auditor-a", "auditor-b"])
    assert len(created) == 2
    for task in created:
        assert task.type == "pr_review"
        assert task.prs_json and "42" in task.prs_json
        assert task.publish_mode == "manual"
        # The review brief is the default; the queue appends custom_instructions
        # once at run time (same contract as every catalog agent).
        assert task.prompt == reviews.DEFAULT_REVIEW_PROMPT
        assert task.agent_id in ("auditor-a", "auditor-b")
    assignments = reviews.assignments_for_pr(session, repo.id, 42)
    assert {a.agent_id for a in assignments} == {"auditor-a", "auditor-b"}
    assert all(a.status == "queued" for a in assignments)
    assert {a.task_id for a in assignments} == {t.id for t in created}


def test_assign_reviewers_requires_reviewer_kind(session) -> None:
    repo = _repo(session)
    catalog.create_agent(session, id="general-agent", name="G", kind="general", enabled=True)
    with pytest.raises(ReviewError, match="not 'reviewer'"):
        reviews.assign_reviewers(session, repo, 1, ["general-agent"])


def test_assign_reviewers_rejects_unknown_or_disabled(session) -> None:
    repo = _repo(session)
    _reviewer_agent(session, "enabled-auditor")
    catalog.create_agent(session, id="disabled-auditor", name="D", kind="reviewer", enabled=False)
    with pytest.raises(ReviewError, match="catalog agent not found"):
        reviews.assign_reviewers(session, repo, 1, ["nope"])
    with pytest.raises(ReviewError, match="disabled"):
        reviews.assign_reviewers(session, repo, 1, ["disabled-auditor"])
    with pytest.raises(ReviewError, match="no reviewers selected"):
        reviews.assign_reviewers(session, repo, 1, [])


def test_default_review_prompt_used(session) -> None:
    """The review brief is the default prompt (custom_instructions ride the
    task prompt once at run time)."""
    repo = _repo(session)
    catalog.create_agent(
        session,
        id="quiet-reviewer",
        name="Quiet",
        kind="reviewer",
        personality_md="Review.",
        enabled=True,
    )
    created = reviews.assign_reviewers(session, repo, 7, ["quiet-reviewer"])
    assert created[0].prompt == reviews.DEFAULT_REVIEW_PROMPT


def test_assign_reviewers_dedupes(session) -> None:
    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")
    _reviewer_agent(session, "auditor-b")
    created = reviews.assign_reviewers(session, repo, 4, ["auditor-a", "auditor-a", "auditor-b"])
    assert {t.agent_id for t in created} == {"auditor-a", "auditor-b"}


def test_cleanup_partial_removes_orphan_tasks(session) -> None:
    """A failed batch must not leave already-created reviewer tasks behind."""
    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")
    (created,) = reviews.assign_reviewers(session, repo, 6, ["auditor-a"])
    reviews._cleanup_partial(session, [created])
    from jalebi.db import Task

    assert session.get(Task, created.id) is None
    assert reviews.assignments_for_pr(session, repo.id, 6) == []


def test_assignment_status_transitions(session) -> None:
    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")
    (created,) = reviews.assign_reviewers(session, repo, 5, ["auditor-a"])
    assignment = reviews.assignment_by_task(session, created.id)
    assert assignment is not None and assignment.status == "queued"

    reviews.set_assignment_status(session, created.id, "running")
    assignment = reviews.assignment_by_task(session, created.id)
    assert assignment is not None and assignment.status == "running"

    reviews.set_assignment_status(session, created.id, "posted")
    posted = reviews.assignment_by_task(session, created.id)
    assert posted is not None and posted.status == "posted"

    with pytest.raises(ReviewError):
        reviews.set_assignment_status(session, created.id, "bogus")


def test_assignment_dict_includes_agent_name(session) -> None:
    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")
    (created,) = reviews.assign_reviewers(session, repo, 9, ["auditor-a"])
    assignment = reviews.assignment_by_task(session, created.id)
    assert assignment is not None
    data = reviews.assignment_to_dict(session, assignment)
    assert data["agent_name"] == "Security Auditor"
    assert data["agent_id"] == "auditor-a"
    assert data["task_id"] == created.id
    assert data["pr_number"] == 9
