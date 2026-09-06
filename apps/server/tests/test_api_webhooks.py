"""Tests for the webhook listener + trigger-rule API routes (PRD F14)."""

import hashlib
import hmac
import json

import pytest

from jalebi import repos, secrets, settings, webhooks


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _token(app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")


@pytest.fixture
def repo(session) -> int:
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    return row.id


PR_PAYLOAD = {
    "action": "opened",
    "repository": {"full_name": "owner/repo", "id": 1},
    "pull_request": {
        "number": 5,
        "title": "Add feature",
        "base": {"ref": "main"},
        "head": {"ref": "feature/x"},
        "user": {"login": "bob"},
        "labels": [],
    },
}


def test_webhook_creates_review_tasks_via_rule(client, session, repo) -> None:
    """pull_request.opened + start_review rule → reviewer tasks created + enqueued."""
    from jalebi import catalog

    catalog.create_agent(
        session, id="auditor-a", name="A", kind="reviewer", enabled=True
    )
    webhooks.create_rule(
        session,
        repo_id=repo,
        event="pull_request.opened",
        action="start_review",
        agent_ids=["auditor-a"],
    )

    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-1", "X-GitHub-Event": "pull_request"},
        json=PR_PAYLOAD,
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["matched"] == 1
    assert body["results"][0]["action"] == "start_review"
    assert body["results"][0]["work"][0]["type"] == "review"

    # Delivery recorded.
    deliveries = webhooks.list_deliveries(session)
    assert len(deliveries) == 1
    assert deliveries[0].github_delivery_id == "d-1"
    assert deliveries[0].status == "matched"


def test_webhook_deduplicates_redelivery(client, session, repo) -> None:
    """A re-delivery with the same X-GitHub-Delivery id is a no-op."""
    from jalebi import catalog

    catalog.create_agent(session, id="auditor-a", name="A", kind="reviewer", enabled=True)
    webhooks.create_rule(
        session, repo_id=repo, event="pull_request.opened", action="start_review",
        agent_ids=["auditor-a"],
    )
    first = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-1", "X-GitHub-Event": "pull_request"},
        json=PR_PAYLOAD,
    )
    assert first.get_json()["matched"] == 1

    second = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-1", "X-GitHub-Event": "pull_request"},
        json=PR_PAYLOAD,
    )
    assert second.status_code == 200
    assert second.get_json()["deduplicated"] is True
    # Only one delivery row, and only the original tasks were created.
    assert len(webhooks.list_deliveries(session)) == 1
    from jalebi import tasks as tasks_service

    assert len(tasks_service.list_tasks(session)) == 1


def test_webhook_signature_verified(client, session, repo) -> None:
    """With a secret configured, an invalid X-Hub-Signature-256 is rejected."""
    settings.set_setting(session, "webhook_secret", "s3cret")
    body = json.dumps(PR_PAYLOAD).encode()
    headers = {"X-GitHub-Delivery": "d-2", "X-GitHub-Event": "pull_request"}
    bad = client.post(
        "/webhook", headers={**headers, "X-Hub-Signature-256": "sha256=" + "0" * 64}, data=body,
        content_type="application/json",
    )
    assert bad.status_code == 403
    good = client.post(
        "/webhook", headers={**headers, "X-Hub-Signature-256": _sig("s3cret", body)}, data=body,
        content_type="application/json",
    )
    assert good.status_code == 200


def test_webhook_ignores_unconnected_repo(client, session) -> None:
    payload = dict(PR_PAYLOAD)
    payload["repository"] = {"full_name": "other/repo", "id": 2}
    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-3", "X-GitHub-Event": "pull_request"},
        json=payload,
    )
    assert res.status_code == 200
    assert res.get_json()["matched"] is False
    deliveries = webhooks.list_deliveries(session)
    assert deliveries[0].status == "ignored"


def test_trigger_rule_crud(client, session, repo) -> None:
    res = client.post(
        "/api/triggers",
        json={
            "repo_id": repo,
            "event": "issues.opened",
            "action": "triage_issue",
            "custom_instructions": "Fix it.",
            "enabled": True,
        },
    )
    assert res.status_code == 201
    rule = res.get_json()
    assert rule["event"] == "issues.opened"
    assert rule["action"] == "triage_issue"
    assert rule["label_filter"] == []

    rule_id = rule["id"]
    res = client.get("/api/triggers")
    assert [r["id"] for r in res.get_json()] == [rule_id]

    res = client.put(f"/api/triggers/{rule_id}", json={"enabled": False})
    assert res.status_code == 200
    assert res.get_json()["enabled"] is False

    res = client.put("/api/triggers/999", json={"enabled": False})
    assert res.status_code == 404

    res = client.delete(f"/api/triggers/{rule_id}")
    assert res.status_code == 200
    assert res.get_json() == {"deleted": rule_id}
    assert client.delete(f"/api/triggers/{rule_id}").status_code == 404


def test_trigger_rule_validation(client, session, repo) -> None:
    res = client.post(
        "/api/triggers",
        json={"repo_id": repo, "event": "pull_request.opened", "action": "bogus"},
    )
    assert res.status_code == 400
    assert "invalid action" in res.get_json()["error"]

    res = client.post(
        "/api/triggers",
        json={"repo_id": repo, "event": "pull_request.opened", "action": "start_review"},
    )
    assert res.status_code == 400
    assert "requires agent_ids" in res.get_json()["error"]


def test_webhook_status(client, session, repo) -> None:
    settings.set_setting(session, "webhook_url", "https://tunnel.example.com")
    settings.set_setting(session, "webhook_secret", "s")
    res = client.get("/api/webhook/status")
    assert res.status_code == 200
    body = res.get_json()
    assert body["url"] == "https://tunnel.example.com"
    assert body["reachable"] is True
    assert body["secret_set"] is True
    assert [r["full_name"] for r in body["repos"]] == ["owner/repo"]


def test_replay_runs_stored_delivery(client, session, repo) -> None:
    from jalebi import catalog

    catalog.create_agent(session, id="auditor-a", name="A", kind="reviewer", enabled=True)
    webhooks.create_rule(
        session, repo_id=repo, event="pull_request.opened", action="start_review",
        agent_ids=["auditor-a"],
    )
    client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-9", "X-GitHub-Event": "pull_request"},
        json=PR_PAYLOAD,
    )
    delivery = webhooks.list_deliveries(session)[0]

    # Replaying a start_review delivery must NOT create a second reviewer task —
    # the agent is already assigned to the PR (dedup on replay).
    res = client.post(f"/api/webhooks/deliveries/{delivery.id}/replay")
    assert res.status_code == 200
    body = res.get_json()
    assert body["matched"] == 1
    assert body["results"][0]["work"] == []
    from jalebi import tasks as tasks_service

    assert len(tasks_service.list_tasks(session)) == 1

    assert client.post("/api/webhooks/deliveries/999/replay").status_code == 404


def test_replay_triage_issue_is_idempotent(client, session, repo) -> None:
    """Replaying a delivery that matched a triage_issue rule must NOT create a
    second issue_fix task (the original delivery's work is replayed as a no-op)."""
    from jalebi import tasks as tasks_service

    webhooks.create_rule(
        session, repo_id=repo, event="issues.opened", action="triage_issue",
        custom_instructions="Fix it.",
    )
    client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-10", "X-GitHub-Event": "issues"},
        json={
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "issue": {"number": 7, "title": "bug", "body": "breaks", "user": {"login": "bob"}},
        },
    )
    assert len(tasks_service.list_tasks(session)) == 1
    delivery = webhooks.list_deliveries(session)[0]

    res = client.post(f"/api/webhooks/deliveries/{delivery.id}/replay")
    assert res.status_code == 200
    body = res.get_json()
    assert body["matched"] == 1
    assert body["results"][0]["note"] == "already dispatched — skipped"
    assert body["results"][0]["work"] == []
    assert len(tasks_service.list_tasks(session)) == 1


def test_replay_create_task_is_idempotent(client, session, repo) -> None:
    """Replaying a delivery that matched a create_task rule must NOT create a
    second freeform task (no duplicate PRs from replay)."""
    from jalebi import tasks as tasks_service

    webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="Sync the changelog.",
    )
    client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-11", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    assert len(tasks_service.list_tasks(session)) == 1
    delivery = webhooks.list_deliveries(session)[0]

    res = client.post(f"/api/webhooks/deliveries/{delivery.id}/replay")
    assert res.status_code == 200
    assert res.get_json()["results"][0]["note"] == "already dispatched — skipped"
    assert len(tasks_service.list_tasks(session)) == 1


def test_replay_runs_rule_added_after_delivery(client, session, repo) -> None:
    """A rule created AFTER the delivery (so it wasn't in the original result)
    still fires on replay — the replay no-op only skips rules already dispatched."""
    from jalebi import tasks as tasks_service

    client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-12", "X-GitHub-Event": "issues"},
        json={
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "issue": {"number": 8, "title": "bug", "body": "breaks", "user": {"login": "bob"}},
        },
    )
    assert len(tasks_service.list_tasks(session)) == 0  # no rule yet → ignored
    delivery = webhooks.list_deliveries(session)[0]

    webhooks.create_rule(
        session, repo_id=repo, event="issues.opened", action="triage_issue",
        custom_instructions="Fix it.",
    )
    res = client.post(f"/api/webhooks/deliveries/{delivery.id}/replay")
    assert res.status_code == 200
    assert res.get_json()["matched"] == 1
    tasks = tasks_service.list_tasks(session)
    assert len(tasks) == 1 and tasks[0].type == "issue_fix"


def test_deliveries_list(client, session, repo) -> None:
    webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="sync",
    )
    client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-7", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    res = client.get("/api/webhooks/deliveries")
    assert res.status_code == 200
    items = res.get_json()
    assert len(items) == 1
    assert items[0]["github_delivery_id"] == "d-7"
    assert items[0]["event"] == "push"


def test_webhook_registration_requires_url(client, session, repo) -> None:
    """Without webhook_url, registration is refused with a clear error."""
    res = client.post(f"/api/repos/{repo}/webhook")
    assert res.status_code == 409
    assert "webhook_url" in res.get_json()["error"]


def test_webhook_secret_write_only(client, session, repo) -> None:
    """GET /api/settings never returns the webhook_secret value; re-saving the
    masked placeholder does not clobber it; empty string clears it."""
    settings.set_setting(session, "webhook_secret", "s3cret-value")
    res = client.get("/api/settings")
    assert res.status_code == 200
    body = res.get_json()
    assert body["webhook_secret"] == "••••••••"
    assert "s3cret-value" not in json.dumps(body)

    # Re-submitting the masked placeholder is a no-op (value preserved).
    res = client.post("/api/settings", json={"key": "webhook_secret", "value": "••••••••"})
    assert res.status_code == 200
    assert settings.get_setting(session, "webhook_secret") == "s3cret-value"

    # Empty string clears it.
    res = client.post("/api/settings", json={"key": "webhook_secret", "value": ""})
    assert res.status_code == 200
    assert settings.get_setting(session, "webhook_secret") == ""


def test_webhook_password_requires_secret(client, app, session, repo) -> None:
    """A password-protected install must refuse unsigned webhook deliveries."""

    app.config["JALEBI_CONFIG"] = app.config["JALEBI_CONFIG"].__class__(
        host="127.0.0.1", port=3456, data_dir=app.config["JALEBI_CONFIG"].data_dir,
        password="pw",
    )
    # No webhook_secret set → the listener refuses.
    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-x", "X-GitHub-Event": "pull_request"},
        json=PR_PAYLOAD,
    )
    assert res.status_code == 403
    assert "webhook_secret" in res.get_json()["error"]


def test_webhook_triage_masks_issue_body(client, session, repo) -> None:
    """A secret value in a webhook issue body is masked before it reaches the
    task context (which flows into the worktree AGENTS.md)."""
    webhooks.create_rule(
        session, repo_id=repo, event="issues.opened", action="triage_issue",
        custom_instructions="fix it",
    )
    secrets.add_github_token(
        client.application.config["JALEBI_CONFIG"], "leaky", "ghp_leak_value"
    )
    payload = {
        "action": "opened",
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 4, "title": "Bug", "body": "token is ghp_leak_value here"},
    }
    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-mask", "X-GitHub-Event": "issues"},
        json=payload,
    )
    assert res.status_code == 200

    from jalebi import tasks as tasks_service

    task = tasks_service.list_tasks(session)[-1]
    assert task.context_json is not None
    context = json.loads(task.context_json)
    issue = context["issues"][0]
    assert "ghp_leak_value" not in issue["body"]
    assert "***" in issue["body"]


def test_webhook_concurrent_redelivery_only_dispatches_once(
    client, session, repo, monkeypatch
) -> None:
    """Two concurrent deliveries with the same X-GitHub-Delivery id must run the
    rules once (the UNIQUE constraint wins the race, no IntegrityError/500)."""
    from jalebi import catalog

    catalog.create_agent(session, id="auditor-a", name="A", kind="reviewer", enabled=True)
    webhooks.create_rule(
        session, repo_id=repo, event="pull_request.opened", action="start_review",
        agent_ids=["auditor-a"],
    )

    import threading

    results: list[int] = []
    def fire():
        r = client.post(
            "/webhook",
            headers={"X-GitHub-Delivery": "race-1", "X-GitHub-Event": "pull_request"},
            json=PR_PAYLOAD,
        )
        results.append(r.status_code)

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(code == 200 for code in results)
    # Exactly one delivery row and exactly one reviewer task.
    assert len(webhooks.list_deliveries(session)) == 1
    from jalebi import tasks as tasks_service

    assert len(tasks_service.list_tasks(session)) == 1


def test_all_rules_error_marks_delivery_failed(client, session, repo, monkeypatch) -> None:
    """A delivery whose rules only returned error work must record status=failed
    (not the prior hardcoded "matched"), so the Triggers page Delivery log
    doesn't lie about success."""
    from jalebi import tasks as tasks_service

    # create_task rules now require instructions at creation; force the runtime
    # error by making the task insert itself fail.
    rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="Sync the changelog.",
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(tasks_service, "create_task", _boom)

    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-h1-1", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    body = res.get_json()
    assert res.status_code == 200
    assert body["ok"] is True
    assert body["matched"] is False
    assert len(body["results"]) == 1
    assert body["results"][0]["rule_id"] == rule.id
    assert body["results"][0]["work"] == [
        {"type": "error", "error": "db is on fire"}
    ]
    delivery = webhooks.list_deliveries(session)[0]
    assert delivery.status == "failed"
    stored: dict = json.loads(delivery.result or "{}")
    assert stored["rules"][0]["rule_id"] == rule.id
    assert stored["rules"][0]["work"][0]["type"] == "error"
    assert len(tasks_service.list_tasks(session)) == 0


def test_mixed_one_ok_one_error_keeps_matched(client, session, repo, monkeypatch) -> None:
    """When at least one rule produces real work and another errors, the
    delivery stays status=matched (regression guard vs over-correction)."""
    from jalebi import tasks as tasks_service

    ok_rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="Sync the changelog.",
    )
    err_rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="ERR boom.",
    )

    real_create = tasks_service.create_task

    def _selective_boom(*args, **kwargs):
        if "ERR" in str(kwargs.get("prompt", "")):
            raise RuntimeError("boom")
        return real_create(*args, **kwargs)

    monkeypatch.setattr(tasks_service, "create_task", _selective_boom)

    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-h1-2", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    body = res.get_json()
    assert res.status_code == 200
    assert body["ok"] is True
    assert body["matched"] is True
    assert len(body["results"]) == 2
    by_id = {r["rule_id"]: r for r in body["results"]}
    assert by_id[ok_rule.id]["work"][0]["type"] == "freeform"
    assert by_id[err_rule.id]["work"][0]["type"] == "error"
    delivery = webhooks.list_deliveries(session)[0]
    assert delivery.status == "matched"
    stored_mixed: dict = json.loads(delivery.result or "{}")
    assert {e["rule_id"] for e in stored_mixed["rules"]} == {ok_rule.id, err_rule.id}
    assert len(tasks_service.list_tasks(session)) == 1


def test_webhook_empty_work_marks_delivery_failed(client, session, repo) -> None:
    """A rule that matched but returned empty work (e.g. rerun_review with no
    prior assignments and no pr_number in the payload) must also be recorded
    as failed, not matched."""
    from jalebi import tasks as tasks_service

    rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="rerun_review",
    )

    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-h1-3", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    body = res.get_json()
    assert res.status_code == 200
    assert body["matched"] is False
    delivery = webhooks.list_deliveries(session)[0]
    assert delivery.status == "failed"
    stored: dict = json.loads(delivery.result or "{}")
    assert stored["rules"][0]["work"] == []
    assert len(tasks_service.list_tasks(session)) == 0
    assert rule.id == body["results"][0]["rule_id"]


def test_replay_of_errored_delivery_retries_rule(
    client, session, repo, monkeypatch
) -> None:
    """A delivery whose rule stored error-only work must be RETRIED on replay
    (the attempt produced nothing to duplicate), not skipped."""
    from jalebi import tasks as tasks_service

    webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="Sync the changelog.",
    )

    calls = {"n": 0}
    real_create = tasks_service.create_task

    def _fail_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient lock")
        return real_create(*args, **kwargs)

    monkeypatch.setattr(tasks_service, "create_task", _fail_once)

    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-h3-err", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    body = res.get_json()
    assert res.status_code == 200
    assert body["matched"] is False
    assert body["results"][0]["work"][0]["type"] == "error"
    delivery = webhooks.list_deliveries(session)[0]
    assert delivery.status == "failed"
    assert len(tasks_service.list_tasks(session)) == 0

    replay_res = client.post(f"/api/webhooks/deliveries/{delivery.id}/replay")
    assert replay_res.status_code == 200
    replay_body = replay_res.get_json()
    assert replay_body["matched"] == 1
    assert "note" not in replay_body["results"][0]  # retried, not skipped
    assert replay_body["results"][0]["work"][0]["type"] == "freeform"
    assert len(tasks_service.list_tasks(session)) == 1

    # The retried outcome is persisted; a second replay skips (no duplicates).
    # (expire: the route commits on its own session; this session cached the row.)
    session.expire_all()
    stored: dict = json.loads(
        webhooks.list_deliveries(session)[0].result or "{}"
    )
    assert stored["rules"][0]["work"][0]["type"] == "freeform"
    assert webhooks.list_deliveries(session)[0].status == "matched"
    replay2 = client.post(f"/api/webhooks/deliveries/{delivery.id}/replay")
    assert replay2.get_json()["results"][0]["note"] == "already dispatched — skipped"
    assert len(tasks_service.list_tasks(session)) == 1


def test_replay_of_empty_work_delivery_retries_without_duplicates(
    client, session, repo, monkeypatch
) -> None:
    """A rule whose stored work is ``[]`` (e.g. rerun_review with nothing to
    re-run) is re-evaluated on replay — and still creates nothing. The outcome
    is persisted so the log reflects the replay."""
    from jalebi import tasks as tasks_service

    webhooks.create_rule(
        session, repo_id=repo, event="push", action="rerun_review",
    )

    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-h3-emp", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    body = res.get_json()
    assert res.status_code == 200
    assert body["matched"] is False
    delivery = webhooks.list_deliveries(session)[0]
    assert delivery.status == "failed"
    stored: dict = json.loads(delivery.result or "{}")
    assert stored["rules"][0]["work"] == []

    calls = {"n": 0}
    real_dispatch = webhooks.dispatch_rule

    def _counting_dispatch(*args, **kwargs):
        calls["n"] += 1
        return real_dispatch(*args, **kwargs)

    monkeypatch.setattr(webhooks, "dispatch_rule", _counting_dispatch)

    replay_res = client.post(f"/api/webhooks/deliveries/{delivery.id}/replay")
    assert replay_res.status_code == 200
    replay_body = replay_res.get_json()
    assert replay_body["matched"] == 1
    assert calls["n"] == 1  # re-evaluated, not skipped
    assert "note" not in replay_body["results"][0]
    assert replay_body["results"][0]["work"] == []
    assert len(tasks_service.list_tasks(session)) == 0
    # Persisted back onto the delivery.
    assert webhooks.list_deliveries(session)[0].result is not None

def test_completed_delivery_with_multiple_rules_stores_all_in_result(
    client, session, repo
) -> None:
    """A delivery matching 2 rules must record BOTH rule_ids in
    result.rules[] — the single-rule matched_rule_id column was the flaw
    (Step 45/M5 dropped that column)."""
    from jalebi import tasks as tasks_service

    r1 = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="A",
    )
    r2 = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="B",
    )

    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-multi", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    assert res.get_json()["matched"] is True
    delivery = webhooks.list_deliveries(session)[0]
    stored: dict = json.loads(delivery.result or "{}")
    assert {e["rule_id"] for e in stored["rules"]} == {r1.id, r2.id}
    assert len(tasks_service.list_tasks(session)) == 2
    # matched_rule_id column no longer exists on the model.
    assert not hasattr(delivery, "matched_rule_id")


def test_status_delivery_nudges_task_without_rules(client, session, repo) -> None:
    """Phase 4 T4.2 wiring: a failing `status` event on a task branch
    enqueues a nudge follow-up even when NO trigger rule matches (the
    nudger is independent of rules). Uses the real GitHub payload shape
    (branches as objects)."""
    from jalebi import nudger
    from jalebi import tasks as tasks_service
    from jalebi.db import Run

    settings.set_setting(session, "auto_nudge", True)
    task = tasks_service.create_task(
        session, type_="freeform", repo_id=repo, prompt="x"
    )
    task.status = "failed"
    session.add(Run(task_id=task.id, seq=1, status="failed", session_id="ses_1"))
    session.commit()

    q = client.application.config["JALEBI_QUEUE"]
    calls: list = []
    orig = q.enqueue_followup
    q.enqueue_followup = lambda *a, **k: calls.append((a, k))  # type: ignore[assignment]
    try:
        res = client.post(
            "/webhook",
            headers={"X-GitHub-Delivery": "d-nudge-1", "X-GitHub-Event": "status"},
            json={
                "sha": "deadbeef",
                "state": "failure",
                "branches": [{"name": f"jalebi/{task.id}"}],
                "repository": {"full_name": "owner/repo"},
            },
        )
    finally:
        q.enqueue_followup = orig  # type: ignore[assignment]
    assert res.status_code == 200
    assert res.get_json()["matched"] is False  # no rules — nudge still fired
    assert len(calls) == 1
    assert nudger._already_nudged(
        session, task.id, f"{task.id}:ci_failure:deadbeef:failure"
    )


def test_status_delivery_cannot_nudge_another_repos_task(client, session, repo, monkeypatch):
    from jalebi import tasks as tasks_service
    from jalebi.db import Nudge, Run

    settings.set_setting(session, "auto_nudge", True)
    repos.upsert_repo(
        session, full_name="owner/other", default_branch="main",
        clone_url="https://github.com/owner/other.git", pat_name="test",
    )
    task = tasks_service.create_task(session, type_="freeform", repo_id=repo, prompt="x")
    task.status = "failed"
    session.add(Run(task_id=task.id, seq=1, status="failed", session_id="ses_1"))
    session.commit()
    calls = []
    q = client.application.config["JALEBI_QUEUE"]
    monkeypatch.setattr(q, "enqueue_followup", lambda *a, **k: calls.append(a))

    payload = {
        "sha": "deadbeef", "state": "failure",
        "branches": [{"name": f"jalebi/{task.id}"}],
        "repository": {"full_name": "owner/other"},
    }
    response = client.post(
        "/webhook", json=payload,
        headers={"X-GitHub-Delivery": "other-repo", "X-GitHub-Event": "status"},
    )
    assert response.status_code == 200
    assert calls == []
    assert session.query(Nudge).count() == 0

    # The identical branch and failure in the task's own repo still work.
    payload["repository"]["full_name"] = "owner/repo"
    response = client.post(
        "/webhook", json=payload,
        headers={"X-GitHub-Delivery": "own-repo", "X-GitHub-Event": "status"},
    )
    assert response.status_code == 200
    assert calls[0][0] == task.id
    assert len(calls) == 1
    assert session.query(Nudge).count() == 1


def test_pull_request_review_rule_fires_on_submitted(client, session, repo) -> None:
    """A bare `pull_request_review` rule must fire when GitHub delivers
    `pull_request_review` + action `submitted` (the event key is suffixed, the
    rule event is not — exact matching used to drop these silently)."""
    from jalebi import tasks as tasks_service

    webhooks.create_rule(
        session, repo_id=repo, event="pull_request_review",
        action="create_task", custom_instructions="Review follow-up.",
    )
    payload = {
        "action": "submitted",
        "repository": {"full_name": "owner/repo"},
        "pull_request": {
            "number": 7, "title": "T", "base": {"ref": "main"},
            "head": {"ref": "feature/y"}, "user": {"login": "bob"}, "labels": [],
        },
        "review": {"state": "commented", "body": "nice"},
    }
    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-review-1", "X-GitHub-Event": "pull_request_review"},
        json=payload,
    )
    body = res.get_json()
    assert res.status_code == 200
    assert body["matched"] is True
    assert body["results"][0]["work"][0]["type"] == "freeform"
    assert len(tasks_service.list_tasks(session)) == 1
    assert webhooks.list_deliveries(session)[0].status == "matched"


def test_update_rule_null_clears_branch_filter(client, session, repo) -> None:
    """PUT with an explicit null clears an optional field; omitted keys are
    left alone (previously neither path could clear anything)."""
    rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        branch_filter="main", custom_instructions="Do it.",
    )
    res = client.put(
        f"/api/triggers/{rule.id}",
        json={"author_filter": "octocat"},
    )
    assert res.status_code == 200
    assert res.get_json()["branch_filter"] == "main"  # omitted → kept
    assert res.get_json()["author_filter"] == "octocat"

    res = client.put(f"/api/triggers/{rule.id}", json={"branch_filter": None})
    assert res.status_code == 200
    assert res.get_json()["branch_filter"] is None


def test_rerun_review_resets_assignment_status(client, session, repo) -> None:
    """Re-enqueueing a review task must move its assignment back to queued —
    otherwise the registry reports posted/failed while the pass is waiting."""
    from jalebi import catalog, reviews

    catalog.create_agent(session, id="auditor-a", name="A", kind="reviewer", enabled=True)
    row = session.get(repos.Repo, repo)
    (task,) = reviews.assign_reviewers(session, row, 9, ["auditor-a"])
    task.status = "done"
    session.commit()
    assignment = reviews.assignment_by_task(session, task.id)
    assert assignment is not None
    # Simulate the finished first pass the rerun is meant to repeat.
    assignment.status = "posted"
    session.commit()

    webhooks.create_rule(
        session, repo_id=repo, event="pull_request.synchronize", action="rerun_review"
    )
    payload = {
        "action": "synchronize",
        "repository": {"full_name": "owner/repo"},
        "pull_request": {
            "number": 9, "title": "T", "base": {"ref": "main"},
            "head": {"ref": "feature/z"}, "user": {"login": "bob"}, "labels": [],
        },
    }
    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-rerun-1", "X-GitHub-Event": "pull_request"},
        json=payload,
    )
    assert res.status_code == 200
    assert res.get_json()["matched"] is True
    assert session.get(repos.Repo, repo) is not None
    session.expire_all()
    refetched = reviews.assignment_by_task(session, task.id)
    assert refetched is not None and refetched.status == "queued"


def test_create_rule_rejects_missing_instructions_and_bad_lists(client, session, repo) -> None:
    """create_task/triage_issue without a prompt, and non-list filters/agent
    ids, are 400s at save time — not silent failures at runtime."""
    res = client.post(
        "/api/triggers",
        json={"repo_id": repo, "event": "push", "action": "create_task"},
    )
    assert res.status_code == 400
    assert "custom_instructions" in res.get_json()["error"]

    res = client.post(
        "/api/triggers",
        json={"repo_id": repo, "event": "push", "action": "create_task",
              "custom_instructions": "Do it.", "label_filter": "bug"},
    )
    assert res.status_code == 400
    assert "label_filter" in res.get_json()["error"]

    rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="Do it.",
    )
    res = client.put(f"/api/triggers/{rule.id}", json={"action": "start_review"})
    assert res.status_code == 400
    assert "agent_ids" in res.get_json()["error"]


def test_triage_uses_repo_default_branch(client, session, repo) -> None:
    """Webhook-created tasks must target the repo's real default branch, not a
    hardcoded `main` (Codex-confirmed: otherwise missing-origin failures or
    PRs against the wrong branch)."""
    from jalebi import tasks as tasks_service

    row = session.get(repos.Repo, repo)
    assert row is not None
    row.default_branch = "trunk"
    session.commit()
    webhooks.create_rule(
        session, repo_id=repo, event="issues.opened",
        action="triage_issue", custom_instructions="Fix it.",
    )
    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-branch-1", "X-GitHub-Event": "issues"},
        json={"action": "opened", "repository": {"full_name": "owner/repo"},
              "issue": {"number": 3, "title": "T", "body": "B",
                        "user": {"login": "ann"}, "labels": []}},
    )
    assert res.status_code == 200
    assert res.get_json()["matched"] is True
    (task,) = tasks_service.list_tasks(session)
    assert task.source_branch == "trunk"
    assert task.target_branch == "trunk"


def test_triggered_by_stamped_and_exposed(client, session, repo) -> None:
    """Dispatched tasks carry their origin event; the task dict exposes it so
    the UI can show which delivery started a task (PRD F14)."""
    from jalebi import tasks as tasks_service

    webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="Sync it.",
    )
    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-origin-1", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    assert res.status_code == 200
    (task,) = tasks_service.list_tasks(session)
    data = tasks_service.task_to_dict(task)
    assert data["triggered_by"] == {
        "delivery_id": "d-origin-1",
        "event": "push",
        "received_at": data["triggered_by"]["received_at"],
    }
    assert data["triggered_by"]["received_at"]


def test_register_adopts_preexisting_hook(client, session, repo, monkeypatch) -> None:
    """When the hook already lives on GitHub (422), registration adopts it
    instead of stranding the repo on a Register button that 502s forever."""
    import jalebi.routes.repos as repos_routes
    from jalebi.github import GitHubError

    settings.set_setting(session, "webhook_url", "https://tunnel.example")

    class _ConflictClient:
        def __init__(self, token):
            pass

        def create_hook(self, *args, **kwargs):
            raise GitHubError("failed to create webhook: HTTP 422")

        def close(self):
            pass

    monkeypatch.setattr(repos_routes, "GitHubClient", _ConflictClient)
    res = client.post(f"/api/repos/{repo}/webhook")
    assert res.status_code == 200
    body = res.get_json()
    assert body["registered"] is True
    assert body["adopted"] is True
    adopted = session.get(repos.Repo, repo)
    assert adopted is not None and adopted.webhook_registered is True


def test_unregister_refuses_without_url_and_clears_gone_hook(
    client, session, repo, monkeypatch
) -> None:
    """Without webhook_url Jalebi can't tell its hook from foreign ones, so it
    must refuse (never guess-delete). When listing succeeds and nothing of ours
    remains, a stuck flag is cleared."""
    import jalebi.routes.repos as repos_routes

    row = session.get(repos.Repo, repo)
    assert row is not None
    row.webhook_registered = True
    session.commit()

    # Replace with a class whose construction explodes if reached.
    class _NopeClient:
        def __init__(self, token):
            raise AssertionError("must not touch GitHub hooks without a URL")

    monkeypatch.setattr(repos_routes, "GitHubClient", _NopeClient)
    res = client.delete(f"/api/repos/{repo}/webhook")
    assert res.status_code == 409
    kept = session.get(repos.Repo, repo)
    assert kept is not None and kept.webhook_registered is True

    settings.set_setting(session, "webhook_url", "https://tunnel.example")

    class _EmptyClient:
        def __init__(self, token):
            pass

        def list_hooks(self, full_name):
            return []

        def close(self):
            pass

    monkeypatch.setattr(repos_routes, "GitHubClient", _EmptyClient)
    res = client.delete(f"/api/repos/{repo}/webhook")
    assert res.status_code == 200
    assert res.get_json()["registered"] is False
    session.expire_all()
    cleared = session.get(repos.Repo, repo)
    assert cleared is not None and cleared.webhook_registered is False


def test_corrupt_delivery_result_does_not_500_deliveries(client, session, repo) -> None:
    """A corrupt result blob degrades to null in the log instead of 500ing the
    whole delivery list."""
    from jalebi import db

    client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-corrupt-1", "X-GitHub-Event": "push"},
        json={"ref": "refs/heads/main", "repository": {"full_name": "owner/repo"}},
    )
    delivery = webhooks.list_deliveries(session)[0]
    row = session.get(db.EventDelivery, delivery.id)
    assert row is not None
    row.result = "{not-json"
    session.commit()

    res = client.get("/api/webhooks/deliveries")
    assert res.status_code == 200
    assert res.get_json()[0]["result"] is None


def test_unknown_api_post_returns_json_404(client) -> None:
    """Unknown write paths answer JSON 404, not HTML 405 (the stale-server
    symptom: the GET catch-all used to claim the path for another method)."""
    res = client.post("/api/definitely-not-a-route", json={})
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_unknown_api_all_write_methods_return_json_404(client) -> None:
    """The JSON 404 covers every write method, not just POST."""
    assert client.put("/api/definitely-not-a-route", json={}).status_code == 404
    assert client.patch("/api/definitely-not-a-route", json={}).status_code == 404
    res = client.delete("/api/definitely-not-a-route")
    assert res.status_code == 404
    assert "error" in res.get_json()


def test_oversized_webhook_body_rejected(client) -> None:
    """The auth-exempt listener caps bodies instead of buffering unboundedly."""
    big = b"x" * (10 * 1024 * 1024 + 1)
    res = client.post(
        "/webhook",
        headers={"X-GitHub-Delivery": "d-big-1", "X-GitHub-Event": "push"},
        data=big,
        content_type="application/json",
    )
    assert res.status_code == 413
    assert "error" in res.get_json()
