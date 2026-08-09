"""Tests for the webhook listener service (PRD F14): signature, dedup, matching, dispatch."""

import hashlib
import hmac

import pytest

from jalebi import webhooks
from jalebi.db import Repo


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _repo(session) -> Repo:
    repo = Repo(
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    return repo


def test_verify_signature() -> None:
    body = b'{"action":"opened"}'
    secret = "s3cret"
    assert webhooks.verify_signature(secret, body, _sig(secret, body)) is True
    assert webhooks.verify_signature(secret, body, "sha256=" + "0" * 64) is False
    assert webhooks.verify_signature(secret, body, None) is False
    assert webhooks.verify_signature(secret, body, "md5=abc") is False
    # No secret configured → disabled (accepted).
    assert webhooks.verify_signature("", body, None) is True


def test_event_context_pull_request() -> None:
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 5,
            "title": "Add feature",
            "body": "body",
            "base": {"ref": "main"},
            "head": {"ref": "jalebi/5"},
            "user": {"login": "bob"},
            "labels": [{"name": "bug"}],
        },
    }
    ctx = webhooks.event_context(payload)
    assert ctx["pr_number"] == 5
    assert ctx["base_ref"] == "main"
    assert ctx["head_ref"] == "jalebi/5"
    assert ctx["author"] == "bob"
    assert ctx["labels"] == ["bug"]


def test_event_context_issue() -> None:
    payload = {
        "action": "opened",
        "issue": {"number": 7, "title": "Bug", "user": {"login": "carol"}},
    }
    ctx = webhooks.event_context(payload)
    assert ctx["issue_number"] == 7
    assert ctx["author"] == "carol"


def test_rule_crud_and_matching(session) -> None:
    repo = _repo(session)
    rule = webhooks.create_rule(
        session,
        repo_id=repo.id,
        event="pull_request.opened",
        action="start_review",
        agent_ids=["auditor-a"],
        branch_filter="main",
        enabled=True,
    )
    assert rule.event == "pull_request.opened"
    ctx = {
        "pr_number": 5,
        "base_ref": "main",
        "head_ref": "jalebi/5",
        "author": "bob",
        "labels": [],
    }
    matched = webhooks.matching_rules(session, repo, "pull_request.opened", ctx)
    assert [m.id for m in matched] == [rule.id]

    # Branch filter mismatch (neither head nor base) → no match.
    ctx_bad = dict(ctx, base_ref="development", head_ref="feature/x")
    assert webhooks.matching_rules(session, repo, "pull_request.opened", ctx_bad) == []

    # Wrong event key → no match.
    assert webhooks.matching_rules(session, repo, "issues.opened", ctx) == []

    # Disabled rule → no match.
    webhooks.update_rule(session, rule.id, enabled=False)
    assert webhooks.matching_rules(session, repo, "pull_request.opened", ctx) == []
    webhooks.update_rule(session, rule.id, enabled=True)

    assert webhooks.delete_rule(session, rule.id) is True
    assert webhooks.delete_rule(session, rule.id) is False


def test_scope_filters_author_and_labels(session) -> None:
    repo = _repo(session)
    webhooks.create_rule(
        session,
        repo_id=repo.id,
        event="pull_request.opened",
        action="create_task",
        author_filter="bob",
        label_filter=["bug", "frontend"],
        custom_instructions="do x",
        enabled=True,
    )
    base = {"pr_number": 1, "base_ref": "main", "author": "bob", "labels": ["bug", "frontend"]}
    assert webhooks.matching_rules(session, repo, "pull_request.opened", base)

    assert (
        webhooks.matching_rules(session, repo, "pull_request.opened", dict(base, author="carol"))
        == []
    )
    assert (
        webhooks.matching_rules(session, repo, "pull_request.opened", dict(base, labels=["bug"]))
        == []
    )


def test_create_rule_validation(session) -> None:
    repo = _repo(session)
    with pytest.raises(webhooks.WebhookError, match="event is required"):
        webhooks.create_rule(session, repo_id=repo.id, event="", action="start_review")
    with pytest.raises(webhooks.WebhookError, match="invalid action"):
        webhooks.create_rule(session, repo_id=repo.id, event="pull_request.opened", action="nuke")
    with pytest.raises(webhooks.WebhookError, match="requires agent_ids"):
        webhooks.create_rule(
            session,
            repo_id=repo.id,
            event="pull_request.opened",
            action="start_review",
        )


def test_delivery_dedup(session) -> None:
    repo = _repo(session)
    webhooks.record_delivery(
        session,
        github_delivery_id="abc123",
        event="pull_request",
        action="opened",
        repo_id=repo.id,
        repo_full_name=repo.full_name,
        payload_json="{}",
        matched_rule_id=None,
        status="ignored",
    )
    assert webhooks.delivery_exists(session, "abc123") is True
    assert webhooks.delivery_exists(session, "nope") is False


def test_dispatch_start_review_creates_tasks(session) -> None:
    from jalebi import catalog

    catalog.create_agent(session, id="auditor-a", name="A", kind="reviewer", enabled=True)
    catalog.create_agent(session, id="auditor-b", name="B", kind="reviewer", enabled=True)
    repo = _repo(session)
    rule = webhooks.create_rule(
        session,
        repo_id=repo.id,
        event="pull_request.opened",
        action="start_review",
        agent_ids=["auditor-a", "auditor-b"],
    )
    ctx = {"pr_number": 5, "base_ref": "main", "author": "bob", "labels": []}

    enqueued: list[int] = []
    class FakeQueue:
        def enqueue(self, task_id):
            enqueued.append(task_id)

    summary = webhooks.dispatch_rule(session, FakeQueue(), rule, repo, ctx)
    assert len(summary) == 2
    assert all(item["type"] == "review" for item in summary)
    assert len(enqueued) == 2
    # Each reviewer got its own pr_review task with an assignment row.
    from jalebi.db import ReviewAssignment

    assignments = list(
        session.execute(
            __import__("sqlalchemy").select(ReviewAssignment).where(
                ReviewAssignment.pr_number == 5
            )
        ).scalars()
    )
    assert {a.agent_id for a in assignments} == {"auditor-a", "auditor-b"}
    assert all(a.status == "queued" for a in assignments)


def test_dispatch_triage_issue_creates_issue_fix(session) -> None:
    repo = _repo(session)
    rule = webhooks.create_rule(
        session, repo_id=repo.id, event="issues.opened", action="triage_issue",
        custom_instructions="Fix the reported bug.",
    )
    ctx = {"issue_number": 3, "title": "Broken", "body": "it breaks", "author": "x", "labels": []}

    enqueued: list[int] = []
    class FakeQueue:
        def enqueue(self, task_id):
            enqueued.append(task_id)

    summary = webhooks.dispatch_rule(session, FakeQueue(), rule, repo, ctx)
    assert summary[0]["type"] == "issue_fix"
    assert len(enqueued) == 1
    from jalebi import tasks

    task = tasks.get_task(session, enqueued[0])
    assert task is not None and task.type == "issue_fix"
    assert task.issues_json and "3" in task.issues_json


def test_dispatch_create_task_freeform(session) -> None:
    repo = _repo(session)
    rule = webhooks.create_rule(
        session, repo_id=repo.id, event="push", action="create_task",
        custom_instructions="Sync the changelog.",
    )
    enqueued: list[int] = []
    class FakeQueue:
        def enqueue(self, task_id):
            enqueued.append(task_id)

    summary = webhooks.dispatch_rule(session, FakeQueue(), rule, repo, {}, masker=None)
    assert summary[0]["type"] == "freeform"
    from jalebi import tasks

    task = tasks.get_task(session, enqueued[0])
    assert task is not None and task.type == "freeform"
    assert task.prompt == "Sync the changelog."


def test_dispatch_rerun_review_reenqueues_terminal(session) -> None:
    from jalebi import catalog, reviews
    from jalebi.db import Task

    catalog.create_agent(session, id="auditor-a", name="A", kind="reviewer", enabled=True)
    repo = _repo(session)
    (task,) = reviews.assign_reviewers(session, repo, 9, ["auditor-a"])
    task.status = "done"
    session.commit()

    rule = webhooks.create_rule(
        session, repo_id=repo.id, event="pull_request.synchronize", action="rerun_review"
    )
    enqueued: list[int] = []
    class FakeQueue:
        def enqueue(self, task_id):
            enqueued.append(task_id)

    summary = webhooks.dispatch_rule(
        session, FakeQueue(), rule, repo, {"pr_number": 9, "base_ref": "main"}, masker=None
    )
    assert summary and summary[0]["type"] == "rerun_review"
    assert enqueued == [task.id]
    fresh = session.get(Task, task.id)
    assert fresh.status == "queued"


@pytest.mark.parametrize(
    "status, expect_enqueued",
    [
        ("done", True),
        ("failed", True),
        ("timed_out", True),
        ("interrupted", True),
        ("cancelled", True),
        ("needs_approval", False),
    ],
)
def test_dispatch_rerun_review_skips_needs_approval(
    session, status: str, expect_enqueued: bool
) -> None:
    """``rerun_review`` re-enqueues tasks in terminal/done statuses but must
    skip ``needs_approval`` — a task mid-publish would race with its own
    in-flight publish if a synchronize webhook fired while the publish was
    running. Other terminal statuses still re-enqueue."""
    from jalebi import catalog, reviews
    from jalebi.db import Task

    catalog.create_agent(session, id="auditor-a", name="A", kind="reviewer", enabled=True)
    repo = _repo(session)
    (task,) = reviews.assign_reviewers(session, repo, 9, ["auditor-a"])
    task.status = status
    session.commit()

    rule = webhooks.create_rule(
        session, repo_id=repo.id, event="pull_request.synchronize", action="rerun_review"
    )
    enqueued: list[int] = []

    class FakeQueue:
        def enqueue(self, task_id):
            enqueued.append(task_id)

    summary = webhooks.dispatch_rule(
        session, FakeQueue(), rule, repo, {"pr_number": 9, "base_ref": "main"}, masker=None
    )
    if expect_enqueued:
        assert summary and summary[0]["type"] == "rerun_review"
        assert enqueued == [task.id]
        fresh = session.get(Task, task.id)
        assert fresh.status == "queued"
    else:
        assert summary == []
        assert enqueued == []
        fresh = session.get(Task, task.id)
        assert fresh.status == "needs_approval"


def test_delivery_to_dict_roundtrip(session) -> None:
    repo = _repo(session)
    d = webhooks.record_delivery(
        session,
        github_delivery_id="d-1",
        event="pull_request",
        action="opened",
        repo_id=repo.id,
        repo_full_name=repo.full_name,
        payload_json='{"a":1}',
        matched_rule_id=None,
        status="matched",
        result={"rules": []},
    )
    data = webhooks.delivery_to_dict(d)
    assert data["github_delivery_id"] == "d-1"
    assert data["status"] == "matched"
    assert data["result"] == {"rules": []}
