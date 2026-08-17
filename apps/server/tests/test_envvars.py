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
        "=novalue\n"
    )
    imported, skipped = envvars.import_env_file(session, content)
    assert imported == 4
    assert skipped == ["BROKEN LINE", "=novalue"]
    rows = {r.name: r.value for r in envvars.list_env_vars(session)}
    assert rows["FOO"] == "bar"
    assert rows["SPACED"] == "a b c"
    assert rows["QUOTED"] == "hi"
    assert rows["KEY"] == "=val"


def test_import_env_file_empty_and_multiline_values(session) -> None:
    """Empty values import as empty; a line with no '=' becomes an empty value;
    multiline values keep their literal text (v1 does not interpret \n escapes
    — documented behavior)."""
    content = (
        "EMPTY=\n"
        'QUOTED="hello world"\n'
        'ML="line1\\nline2"\n'
        "NOEQUALS\n"
    )
    imported, skipped = envvars.import_env_file(session, content)
    assert imported == 4
    assert skipped == []
    rows = {r.name: r.value for r in envvars.list_env_vars(session)}
    assert rows["EMPTY"] == ""
    assert rows["NOEQUALS"] == ""
    assert rows["QUOTED"] == "hello world"
    assert rows["ML"] == "line1\\nline2"  # literal backslash-n, not a newline


def test_import_env_file_skips_bad_lines_and_reports(session) -> None:
    content = "GOOD=1\nINVALID KEY=x\n=novalue\nCOMMENT # not a key\n"
    imported, skipped = envvars.import_env_file(session, content)
    assert imported == 1
    assert skipped == ["INVALID KEY=x", "=novalue", "COMMENT # not a key"]


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


# ---- Phase 4 T1.1: env-var blocklist ---------------------------------------


def test_upsert_rejects_blocked_name(session) -> None:
    import pytest

    with pytest.raises(ValueError, match="blocked"):
        envvars.upsert_env_var(session, name="PATH", value="/evil", repo_id=None)
    with pytest.raises(ValueError, match="blocked"):
        envvars.upsert_env_var(
            session, name="JALEBI_GITHUB_TOKEN", value="x", repo_id=None
        )
    with pytest.raises(ValueError, match="blocked"):
        envvars.upsert_env_var(
            session, name="GIT_CONFIG_KEY_0", value="http.extraHeader", repo_id=None
        )


def test_values_for_names_drops_blocked_with_warning(session, caplog) -> None:
    """A legacy row stored before the blocklist existed must be dropped on read,
    not silently leaked into the agent subprocess env."""
    import logging

    # Bypass upsert's new validation to seed a legacy row that pretends to
    # pre-date the blocklist.
    from jalebi.db import EnvVar

    row = EnvVar(name="PATH", value="/evil", repo_id=None)
    session.add(row)
    session.commit()

    caplog.set_level(logging.WARNING)
    vals = envvars.values_for_names(session, repo_id=None, names=["PATH", "FOO"])
    assert "PATH" not in vals
    assert any("blocked" in rec.message for rec in caplog.records)


def test_import_env_file_skips_blocked_line(session) -> None:
    content = "FOO=bar\nPATH=/evil\nSECRET=v\nGIT_CONFIG_KEY_0=evil\n"
    imported, skipped = envvars.import_env_file(session, content)
    assert imported == 2  # FOO + SECRET only
    assert all("PATH" in s or "GIT_CONFIG_KEY_0" in s for s in skipped)
    names = {r.name for r in envvars.list_env_vars(session)}
    assert "PATH" not in names
    assert "GIT_CONFIG_KEY_0" not in names
    assert names == {"FOO", "SECRET"}


def test_values_for_names_always_drops_jalebi_secrets(session) -> None:
    """JALEBI_* values must never reach the agent env even if they were
    accidentally inserted via a direct INSERT."""
    from jalebi.db import EnvVar

    for n in ("JALEBI_GITHUB_TOKEN", "JALEBI_PORT", "JALEBI_DATA_DIR"):
        session.add(EnvVar(name=n, value="x", repo_id=None))
    session.add(EnvVar(name="OK", value="y", repo_id=None))
    session.commit()

    vals = envvars.values_for_names(
        session,
        repo_id=None,
        names=["JALEBI_GITHUB_TOKEN", "JALEBI_PORT", "JALEBI_DATA_DIR", "OK"],
    )
    assert "OK" in vals
    for n in ("JALEBI_GITHUB_TOKEN", "JALEBI_PORT", "JALEBI_DATA_DIR"):
        assert n not in vals


def test_git_config_prefix_glob_blocks_all(session) -> None:
    """Every GIT_CONFIG_* variant is blocked — there is no legitimate user
    surface for the namespace (Jalebi owns all git auth/config)."""
    blocked_names = [
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_NOSYSTEM",
        "GITCONFIG",  # no underscore → not blocked (negative)
    ]
    for name in blocked_names:
        assert envvars.is_blocked_env_name(name) is name.startswith(
            "GIT_CONFIG_"
        ), f"{name} block status mismatch"
    assert envvars.is_blocked_env_name("PATH") is True
    assert envvars.is_blocked_env_name("GOOD_VAR") is False


def test_api_envvars_rejects_blocked_name(client: FlaskClient, session) -> None:
    """The route maps ``ValueError`` to 400 — verified end-to-end."""
    _repo(session)
    resp = client.post(
        "/api/envvars", json={"name": "PATH", "value": "/evil"}
    )
    assert resp.status_code == 400
    assert "blocked" in resp.get_json()["error"]
