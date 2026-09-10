"""Tests for `/api/agents` catalog routes (PRD F6)."""

import pytest

from jalebi import secrets


@pytest.fixture(autouse=True)
def _token(app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")


def _agent_payload(**overrides) -> dict:
    payload = {
        "id": "security-auditor",
        "name": "Security Auditor",
        "kind": "reviewer",
        "cli": "opencode",
        "model": "openai/gpt-5.1",
        "personality_md": "You are a senior security engineer.",
        "skills": [{"name": "secure-coding", "content": "# Secure coding\n"}],
        "custom_instructions": "Check auth.",
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def test_create_and_list(client) -> None:
    res = client.post("/api/agents", json=_agent_payload())
    assert res.status_code == 201
    body = res.get_json()
    assert body["id"] == "security-auditor"
    assert body["skills"] == [{"name": "secure-coding", "content": "# Secure coding\n"}]

    res = client.get("/api/agents")
    assert res.status_code == 200
    agents = res.get_json()
    assert [a["id"] for a in agents] == ["security-auditor"]

    res = client.get("/api/agents?enabled=1")
    assert [a["id"] for a in res.get_json()] == ["security-auditor"]


def test_create_validates(client) -> None:
    res = client.post("/api/agents", json=_agent_payload(id="Bad Slug!"))
    assert res.status_code == 400
    assert "invalid agent id" in res.get_json()["error"]

    res = client.post("/api/agents", json=_agent_payload(cli="gemini"))
    assert res.status_code == 400

    res = client.post("/api/agents", json=_agent_payload(id="codex-agent", cli="codex"))
    assert res.status_code == 201
    assert res.get_json()["cli"] == "codex"

    res = client.post("/api/agents", json="not-an-object")
    assert res.status_code == 400


def test_create_rejects_non_string_model(client) -> None:
    """A non-string model pin (malformed JSON) is a clean 400, never a 500."""
    res = client.post("/api/agents", json=_agent_payload(model=123))
    assert res.status_code == 400
    assert "model must be a string" in res.get_json()["error"]


def test_get_update_delete(client) -> None:
    client.post("/api/agents", json=_agent_payload())
    res = client.get("/api/agents/security-auditor")
    assert res.status_code == 200
    assert res.get_json()["kind"] == "reviewer"

    res = client.put("/api/agents/security-auditor", json={"kind": "general", "enabled": False})
    assert res.status_code == 200
    assert res.get_json()["kind"] == "general"
    assert res.get_json()["enabled"] is False
    # untouched fields preserved
    assert res.get_json()["name"] == "Security Auditor"

    res = client.put("/api/agents/missing", json={"kind": "general"})
    assert res.status_code == 404

    res = client.delete("/api/agents/security-auditor")
    assert res.status_code == 200
    assert res.get_json() == {"deleted": "security-auditor"}
    assert client.get("/api/agents/security-auditor").status_code == 404
    assert client.delete("/api/agents/security-auditor").status_code == 404


def test_put_validates_skills(client) -> None:
    """A PUT storing an invalid skill name (e.g. path traversal) is refused 400."""
    client.post("/api/agents", json=_agent_payload())
    res = client.put(
        "/api/agents/security-auditor",
        json={"skills": [{"name": "../../escape", "content": "pwned"}]},
    )
    assert res.status_code == 400
    assert "invalid skill name" in res.get_json()["error"]
    # The stored row is unchanged.
    body = client.get("/api/agents/security-auditor").get_json()
    assert body["skills"] == [{"name": "secure-coding", "content": "# Secure coding\n"}]


def test_create_rejects_non_object_skill(client) -> None:
    res = client.post("/api/agents", json=_agent_payload(skills=["not-an-object"]))
    assert res.status_code == 400
    assert "each skill must be an object" in res.get_json()["error"]


def test_put_empty_string_clears_model_pin(client) -> None:
    client.post("/api/agents", json=_agent_payload())
    res = client.put("/api/agents/security-auditor", json={"model": "", "cli": ""})
    assert res.status_code == 200
    body = res.get_json()
    assert body["model"] is None
    assert body["cli"] is None


def test_create_task_with_agent(client, session) -> None:
    from jalebi.db import Repo

    session.add(
        Repo(
            full_name="owner/repo",
            default_branch="main",
            clone_url="https://github.com/owner/repo.git",
            pat_name="test",
        )
    )
    session.commit()
    client.post("/api/agents", json=_agent_payload(id="auditor", kind="general"))
    res = client.post(
        "/api/tasks",
        json={
            "repo_id": 1,
            "type": "freeform",
            "prompt": "do it",
            "agent_id": "auditor",
        },
    )
    assert res.status_code == 201
    body = res.get_json()
    assert body["agent_id"] == "auditor"
    # No cli/model snapshot at creation — the user didn't override, so they stay
    # null and the agent's pins apply at run time (queue._agent_run_opts).
    assert body["model"] is None
    assert body["cli"] is None


def test_create_task_rejects_bad_agent(client, session) -> None:
    from jalebi.db import Repo

    session.add(
        Repo(
            full_name="owner/repo",
            default_branch="main",
            clone_url="https://github.com/owner/repo.git",
            pat_name="test",
        )
    )
    session.commit()
    res = client.post(
        "/api/tasks",
        json={"repo_id": 1, "type": "freeform", "prompt": "do it", "agent_id": "nope"},
    )
    assert res.status_code == 400
    assert "catalog agent not found" in res.get_json()["error"]


def test_create_rejects_strict_types(client) -> None:
    """Booleans must be real booleans and text fields real strings — loose
    coercion either 500s (AttributeError) or silently enables."""
    res = client.post("/api/agents", json=_agent_payload(enabled="false"))
    assert res.status_code == 400
    assert "enabled" in res.get_json()["error"]
    res = client.post("/api/agents", json=_agent_payload(name=123))
    assert res.status_code == 400
    res = client.post("/api/agents", json=_agent_payload(cli=123))
    assert res.status_code == 400


def test_update_cli_switch_drops_stale_model(client) -> None:
    """Switching cli without a new model clears the old pin (it belonged to
    the old backend); an explicit new model in the same call is kept."""
    assert client.post("/api/agents", json=_agent_payload()).status_code == 201
    res = client.put("/api/agents/security-auditor", json={"cli": "codex"})
    assert res.status_code == 200
    assert res.get_json()["cli"] == "codex"
    assert res.get_json()["model"] is None
    res = client.put(
        "/api/agents/security-auditor", json={"cli": "opencode", "model": "m-2"}
    )
    assert (res.get_json()["cli"], res.get_json()["model"]) == ("opencode", "m-2")


def test_agent_usage_reports_tasks_and_rules(client, session) -> None:
    """Usage shows historical task references plus live trigger rules, so the
    UI can warn before a delete that would break rules at dispatch."""
    from jalebi import repos, webhooks
    from jalebi import tasks as tasks_service

    assert client.post("/api/agents", json=_agent_payload()).status_code == 201
    row, _ = repos.upsert_repo(
        session, full_name="owner/repo", default_branch="main",
        clone_url="https://example.com/owner/repo.git", pat_name="test",
    )
    tasks_service.create_task(
        session, type_="freeform", repo_id=row.id, prompt="P",
        agent_id="security-auditor",
    )
    rule = webhooks.create_rule(
        session, repo_id=row.id, event="pull_request.opened",
        action="start_review", agent_ids=["security-auditor"],
    )
    res = client.get("/api/agents/security-auditor/usage")
    assert res.status_code == 200
    body = res.get_json()
    assert body["task_count"] == 1
    assert body["trigger_rules"] == [
        {
            "id": rule.id, "event": "pull_request.opened",
            "action": "start_review", "repo_id": row.id,
            "repo_full_name": "owner/repo",
        }
    ]
    assert client.get("/api/agents/nonexistent/usage").status_code == 404
