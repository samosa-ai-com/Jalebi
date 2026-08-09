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


def test_all_rules_error_marks_delivery_failed(client, session, repo) -> None:
    """A delivery whose rules only returned error work must record status=failed
    (not the prior hardcoded "matched"), so the Triggers page Delivery log
    doesn't lie about success."""
    from jalebi import tasks as tasks_service

    # create_task with no custom_instructions is rejected before create_task is
    # even called (webhooks._dispatch_create_task returns an error list).
    rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
    )

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
        {"type": "error", "error": "create_task requires custom_instructions"}
    ]
    delivery = webhooks.list_deliveries(session)[0]
    assert delivery.status == "failed"
    assert delivery.matched_rule_id == rule.id
    stored: dict = json.loads(delivery.result or "{}")
    assert stored["rules"][0]["work"][0]["type"] == "error"
    assert len(tasks_service.list_tasks(session)) == 0


def test_mixed_one_ok_one_error_keeps_matched(client, session, repo) -> None:
    """When at least one rule produces real work and another errors, the
    delivery stays status=matched (regression guard vs over-correction)."""
    from jalebi import tasks as tasks_service

    ok_rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
        custom_instructions="Sync the changelog.",
    )
    err_rule = webhooks.create_rule(
        session, repo_id=repo, event="push", action="create_task",
    )

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
    assert delivery.matched_rule_id in {ok_rule.id, err_rule.id}
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
