"""Tests for the env-var store (envvars.py service + routes)."""

from flask.testing import FlaskClient

from jalebi import envvars, repos


def _repo(session):
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    return row


def test_upsert_and_list(app, session) -> None:
    _repo(session)
    envvars.upsert_env_var(session, name="DATABASE_URL", value="postgres://x", repo_id=None)
    envvars.upsert_env_var(session, name="API_KEY", value="sk-secret-value", repo_id=None)
    rows = envvars.list_env_vars(session)
    assert {r.name for r in rows} == {"DATABASE_URL", "API_KEY"}

    # Upsert overwrites the value, not a duplicate row.
    envvars.upsert_env_var(session, name="DATABASE_URL", value="postgres://y", repo_id=None)
    rows = envvars.list_env_vars(session)
    assert len(rows) == 2


def test_upsert_rejects_bad_name(session) -> None:
    import pytest

    with pytest.raises(ValueError):
        envvars.upsert_env_var(session, name="BAD NAME", value="x", repo_id=None)
    with pytest.raises(ValueError):
        envvars.upsert_env_var(session, name="A=B", value="x", repo_id=None)


def test_values_for_names_prefers_repo_scope(app, session) -> None:
    repo = _repo(session)
    envvars.upsert_env_var(session, name="FOO", value="global", repo_id=None)
    envvars.upsert_env_var(session, name="FOO", value="repo", repo_id=repo.id)
    envvars.upsert_env_var(session, name="BAR", value="global-only", repo_id=None)

    vals = envvars.values_for_names(session, repo.id, ["FOO", "BAR", "MISSING"])
    assert vals == {"FOO": "repo", "BAR": "global-only"}

    # A different repo sees the global value.
    repo2, _ = repos.upsert_repo(
        session,
        full_name="owner/repo2",
        default_branch="main",
        clone_url="https://github.com/owner/repo2.git",
        pat_name="test",
    )
    vals2 = envvars.values_for_names(session, repo2.id, ["FOO"])
    assert vals2 == {"FOO": "global"}


def test_import_env_file(session) -> None:
    content = (
        "# comment\n"
        "FOO=bar\n"
        "export SPACED='a b c'\n"
        'QUOTED="hi"\n'
        "BROKEN LINE\n"
        "KEY==val\n"
    )
    imported = envvars.import_env_file(session, content)
    assert imported == 4
    rows = {r.name: r.value for r in envvars.list_env_vars(session)}
    assert rows["FOO"] == "bar"
    assert rows["SPACED"] == "a b c"
    assert rows["QUOTED"] == "hi"
    assert rows["KEY"] == "=val"


def test_env_var_to_dict_never_leaks_value(app, session) -> None:
    row = envvars.upsert_env_var(
        session, name="API_KEY", value="super-secret-value-123", repo_id=None
    )
    d = envvars.env_var_to_dict(row)
    assert d["name"] == "API_KEY"
    assert "super-secret-value-123" not in str(d)
    assert "***" in str(d["masked"])


def test_api_envvars_roundtrip_masked(client: FlaskClient, session) -> None:
    _repo(session)
    resp = client.post(
        "/api/envvars",
        json={"name": "DATABASE_URL", "value": "postgres://secret-user:pass@host/db"},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["name"] == "DATABASE_URL"
    assert "secret-user" not in str(body)

    resp = client.get("/api/envvars")
    rows = resp.get_json()
    assert len(rows) == 1
    assert rows[0]["name"] == "DATABASE_URL"
    assert "secret-user" not in str(rows[0])
    assert "***" in rows[0]["masked"]


def test_api_envvars_import(client: FlaskClient, session) -> None:
    _repo(session)
    resp = client.post(
        "/api/envvars/import", json={"content": "FOO=bar\nSECRET=value1\n"}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["imported"] == 2
    assert {r["name"] for r in body["env_vars"]} == {"FOO", "SECRET"}


def test_api_envvars_delete(client: FlaskClient, session) -> None:
    _repo(session)
    created = client.post("/api/envvars", json={"name": "X", "value": "y"}).get_json()
    resp = client.delete(f"/api/envvars/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/envvars").get_json() == []
    assert client.delete(f"/api/envvars/{created['id']}").status_code == 404


def test_api_envvars_invalid_name(client: FlaskClient, session) -> None:
    _repo(session)
    resp = client.post("/api/envvars", json={"name": "BAD NAME", "value": "x"})
    assert resp.status_code == 400
