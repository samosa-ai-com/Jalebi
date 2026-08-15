"""Tests for the reviewer-workflow service (PRD F7)."""

import json

import pytest
from sqlalchemy.exc import IntegrityError

from jalebi import catalog, reviews
from jalebi.db import Repo, ReviewAssignment, Task, now
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


def test_assign_reviewers_conflict_returns_existing_task(session) -> None:
    """A second ``assign_reviewers`` call for the same (repo, pr, agent) must
    recover the existing assignment's task (UNIQUE-constraint race safety net)
    and must not create a duplicate pr_review task."""
    from sqlalchemy import select


    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")

    first = reviews.assign_reviewers(session, repo, 21, ["auditor-a"])
    assert len(first) == 1

    second = reviews.assign_reviewers(session, repo, 21, ["auditor-a"])
    assert len(second) == 1
    assert second[0].id == first[0].id  # returned the existing task
    # Exactly one pr_review task and one assignment row for this PR+agent.
    pr_review_count = session.execute(
        select(Task).where(Task.type == "pr_review")
    ).scalars().all()
    assert len(pr_review_count) == 1
    assert len(reviews.assignments_for_pr(session, repo.id, 21)) == 1


def test_assign_reviewers_concurrent_threads_no_duplicates(session, app) -> None:
    """Two threads racing ``assign_reviewers`` for the same (repo, pr, agent)
    must end up with exactly one pr_review Task and one ReviewAssignment
    (the UNIQUE constraint is the floor)."""
    from sqlalchemy import select


    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")

    import threading

    barrier = threading.Barrier(2)
    results: list[list[int]] = [[], []]

    def worker(idx: int) -> None:
        # Each thread needs its own session bound to the engine — share the
        # sessionmaker but get a fresh Session.
        thread_session = app.config["JALEBI_QUEUE"].__class__.__module__  # noqa: F841
        from jalebi.db import Session as JalebiSession

        s = JalebiSession()
        try:
            barrier.wait(timeout=5)
            created = reviews.assign_reviewers(s, repo, 22, ["auditor-a"])
            results[idx] = [t.id for t in created]
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    flat = [tid for sub in results for tid in sub]
    # Both threads must report the same single task id (the winner's).
    assert len(flat) == 2
    assert flat[0] == flat[1]
    pr_review_count = session.execute(
        select(Task).where(Task.type == "pr_review")
    ).scalars().all()
    assert len(pr_review_count) == 1
    assert len(reviews.assignments_for_pr(session, repo.id, 22)) == 1


def test_unique_constraint_prevents_duplicate_assignment(session) -> None:
    """Direct DB-level: a second INSERT with the same (repo_id, pr_number,
    agent_id) must raise IntegrityError. Proves the migration did its job."""
    import pytest

    from jalebi.db import Task

    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")
    (first,) = reviews.assign_reviewers(session, repo, 23, ["auditor-a"])
    session.expire_all()

    # Bypass assign_reviewers — try to insert a second assignment with the
    # same (repo_id, pr_number, agent_id) directly.
    second = Task(
        type="pr_review",
        repo_id=repo.id,
        prompt="another",
        source_branch="main",
        target_branch="main",
        agent_id="auditor-a",
        pat_name=repo.pat_name,
        prs_json=json.dumps([23]),
        status="queued",
    )
    session.add(second)
    session.commit()
    session.add(
        ReviewAssignment(
            task_id=second.id,
            agent_id="auditor-a",
            pr_number=23,
            repo_id=repo.id,
            status="queued",
            created_at=now(),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    # The first assignment is still the only one in DB.
    assert len(reviews.assignments_for_pr(session, repo.id, 23)) == 1


def test_assign_reviewers_mixed_existing_and_new_partial_success(session) -> None:
    """When the batch has an already-assigned agent AND new agents, the new
    agents must be created and enqueued (the existing one is recovered) —
    the whole batch is NOT dropped on a single collision."""
    from sqlalchemy import select

    from jalebi.db import Task

    repo = _repo(session)
    _reviewer_agent(session, "auditor-a")
    _reviewer_agent(session, "auditor-b")
    _reviewer_agent(session, "auditor-c")
    # Pre-assign A so it'll collide on the next call.
    (a_first,) = reviews.assign_reviewers(session, repo, 30, ["auditor-a"])

    class FakeQueue:
        def __init__(self):
            self.enqueued: list[int] = []

        def enqueue(self, task_id: int) -> None:
            self.enqueued.append(task_id)

    queue = FakeQueue()
    result = reviews.assign_reviewers(
        session, repo, 30, ["auditor-a", "auditor-b", "auditor-c"], queue=queue
    )
    # Returned: A's existing task + B's new task + C's new task — all 3 ids distinct.
    result_ids = [t.id for t in result]
    assert len(result_ids) == 3
    assert len(set(result_ids)) == 3
    assert a_first.id in result_ids
    # Exactly three assignments in DB; one pr_review Task per agent.
    assignments = reviews.assignments_for_pr(session, repo.id, 30)
    assert len(assignments) == 3
    pr_review_tasks = session.execute(
        select(Task).where(Task.type == "pr_review")
    ).scalars().all()
    assert len(pr_review_tasks) == 3
    # Only the NEW tasks (B and C) are enqueued — A's existing task is already in flight.
    assert sorted(queue.enqueued) == sorted(set(result_ids) - {a_first.id})
