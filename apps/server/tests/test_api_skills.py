"""Tests for `/api/skills` routes + agent skill-link fields."""

import pytest

from jalebi import secrets


@pytest.fixture(autouse=True)
def _token(app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")


def _skill_payload(**overrides) -> dict:
    # NOTE: slugs must avoid the real seed library (create_app seeds it).
    payload = {
        "id": "t-secure-coding",
        "name": "T Secure Coding",
        "description": "Security checklist for any codebase.",
        "content": "# Secure coding\nNever use eval.",
        "tags": ["security"],
    }
    payload.update(overrides)
    return payload


def test_skill_crud(client) -> None:
    res = client.post("/api/skills", json=_skill_payload())
    assert res.status_code == 201
    assert res.get_json()["tags"] == ["security"]

    res = client.get("/api/skills")
    assert "t-secure-coding" in [s["id"] for s in res.get_json()]

    res = client.get("/api/skills/t-secure-coding")
    assert res.status_code == 200
    assert res.get_json()["content"].startswith("# Secure coding")

    res = client.put("/api/skills/t-secure-coding", json={"description": "New"})
    assert res.status_code == 200
    assert res.get_json()["description"] == "New"

    res = client.get("/api/skills/t-secure-coding/usage")
    assert res.get_json() == {"agents": []}

    res = client.delete("/api/skills/t-secure-coding")
    assert res.status_code == 200
    assert client.get("/api/skills/t-secure-coding").status_code == 404
    assert client.delete("/api/skills/t-secure-coding").status_code == 404
    assert client.put("/api/skills/t-secure-coding", json={"name": "X"}).status_code == 404


def test_skill_validation(client) -> None:
    res = client.post("/api/skills", json=_skill_payload(id="Bad Slug!"))
    assert res.status_code == 400
    res = client.post("/api/skills", json=_skill_payload(tags="nope"))
    assert res.status_code == 400
    res = client.post("/api/skills", json="not-an-object")
    assert res.status_code == 400


def test_skill_delete_refused_while_linked(client) -> None:
    client.post("/api/skills", json=_skill_payload())
    client.post(
        "/api/agents",
        json={"id": "a", "name": "A", "skill_ids": ["t-secure-coding"]},
    )
    res = client.delete("/api/skills/t-secure-coding")
    assert res.status_code == 409
    assert res.get_json()["agents"] == [{"id": "a", "name": "A"}]
    # Usage names the linking agent for the pre-delete warning.
    res = client.get("/api/skills/t-secure-coding/usage")
    assert res.get_json() == {"agents": [{"id": "a", "name": "A"}]}


def test_agent_skill_links_and_meta(client) -> None:
    client.post("/api/skills", json=_skill_payload())
    res = client.post(
        "/api/agents",
        json={
            "id": "auditor",
            "name": "Auditor",
            "skill_ids": ["t-secure-coding"],
            "description": "Finds vulns.",
            "avatar": "shield",
        },
    )
    assert res.status_code == 201
    body = res.get_json()
    assert body["skill_ids"] == ["t-secure-coding"]
    assert body["skills"] == [
        {"name": "T Secure Coding", "content": "# Secure coding\nNever use eval."}
    ]
    assert body["description"] == "Finds vulns."
    assert body["avatar"] == "shield"

    # Unknown skill links are a clean 400, never a dangling reference.
    res = client.post(
        "/api/agents", json={"id": "b", "name": "B", "skill_ids": ["nope"]}
    )
    assert res.status_code == 400
    assert "unknown skill id" in res.get_json()["error"]

    res = client.put("/api/agents/auditor", json={"skill_ids": "nope"})
    assert res.status_code == 400

    res = client.put("/api/agents/auditor", json={"avatar": "not an id!"})
    assert res.status_code == 400
