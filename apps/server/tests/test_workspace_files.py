"""Phase 4 T7 — in-worktree file browser (read-only)."""

import os
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from jalebi import repos, secrets
from jalebi.config import Config


@pytest.fixture(autouse=True)
def _fake_token(config, monkeypatch):
    secrets.add_github_token(config, "test", "ghp_test")


def _make_task(session) -> int:
    repos.upsert_repo(
        session,
        full_name="owner/r",
        default_branch="main",
        clone_url="https://x/r.git",
        pat_name="test",
    )
    from jalebi import tasks as tasks_svc

    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=1, prompt="x"
    )
    return task.id


def _seed_worktree(config: Config, task_id: int, outside: Path) -> Path:
    """Create a fake worktree with a subdir, text file, binary, .git dir, symlink."""
    wt = Path(config.data_dir) / "ws" / f"task-{task_id}"
    wt.mkdir(parents=True, exist_ok=True)
    (wt / ".git").write_text("gitdir: /tmp/fake")
    (wt / "hello.txt").write_text("hello world\n")
    (wt / "sub").mkdir(parents=True, exist_ok=True)
    (wt / "sub" / "nested.md").write_text("# Nested\n\ncontent\n")
    (wt / "bin.dat").write_bytes(b"\x00\x01\x02\x03 binary")
    (wt / "empty").mkdir(parents=True, exist_ok=True)
    # Symlink escaping the root.
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "secret.txt").write_text("outside\n")
    os.symlink(outside / "secret.txt", wt / "link")
    return wt


def test_list_root_returns_entries_folders_first(
    client: FlaskClient, session, app, config: Config, tmp_path
) -> None:
    task_id = _make_task(session)
    _seed_worktree(config, task_id, tmp_path / "outside")
    resp = client.get(f"/api/tasks/{task_id}/files")
    assert resp.status_code == 200
    body = resp.get_json()
    entries = body["entries"]
    # Folders first, then files, alphabetical; .git + symlink excluded.
    names = [e["name"] for e in entries]
    assert names == ["empty", "sub", "bin.dat", "hello.txt"]
    sub = next(e for e in entries if e["name"] == "sub")
    assert sub["is_dir"] is True
    hello = next(e for e in entries if e["name"] == "hello.txt")
    assert hello["is_dir"] is False
    assert hello["size"] == len("hello world\n")
    assert hello["extension"] == "txt"


def test_list_subdir(client: FlaskClient, session, app, config: Config, tmp_path) -> None:
    task_id = _make_task(session)
    _seed_worktree(config, task_id, tmp_path / "outside")
    resp = client.get(f"/api/tasks/{task_id}/files?path=sub")
    assert resp.status_code == 200
    entries = resp.get_json()["entries"]
    assert [e["name"] for e in entries] == ["nested.md"]
    assert entries[0]["extension"] == "md"


def test_list_empty_dir(client: FlaskClient, session, app, config: Config, tmp_path) -> None:
    task_id = _make_task(session)
    _seed_worktree(config, task_id, tmp_path / "outside")
    resp = client.get(f"/api/tasks/{task_id}/files?path=empty")
    assert resp.status_code == 200
    assert resp.get_json()["entries"] == []


def test_git_traversal_rejected(
    client: FlaskClient, session, app, config: Config, tmp_path
) -> None:
    task_id = _make_task(session)
    _seed_worktree(config, task_id, tmp_path / "outside")
    resp = client.get(f"/api/tasks/{task_id}/files?path=.git")
    assert resp.status_code == 400
    assert "inside .git" in resp.get_json()["error"].lower()


def test_dotdot_traversal_rejected(
    client: FlaskClient, session, app, config: Config, tmp_path
) -> None:
    task_id = _make_task(session)
    _seed_worktree(config, task_id, tmp_path / "outside")
    resp = client.get(f"/api/tasks/{task_id}/files?path=../outside")
    assert resp.status_code == 400
    assert "escapes" in resp.get_json()["error"].lower()


def test_symlink_rejected(
    client: FlaskClient, session, app, config: Config, tmp_path
) -> None:
    task_id = _make_task(session)
    _seed_worktree(config, task_id, tmp_path / "outside")
    resp = client.get(f"/api/tasks/{task_id}/files?path=link")
    assert resp.status_code == 400
    assert "symlink" in resp.get_json()["error"].lower()


def test_binary_file_returns_415(
    client: FlaskClient, session, app, config: Config, tmp_path
) -> None:
    task_id = _make_task(session)
    _seed_worktree(config, task_id, tmp_path / "outside")
    resp = client.get(f"/api/tasks/{task_id}/files/content?path=bin.dat")
    assert resp.status_code == 415
    assert resp.get_json()["binary"] is True


def test_text_file_masked(
    client: FlaskClient, session, app, config: Config, tmp_path
) -> None:
    task_id = _make_task(session)
    wt = _seed_worktree(config, task_id, tmp_path / "outside")
    # Write a file containing the token value (ghp_test) to verify masking.
    (wt / "secret.txt").write_text("token ghp_test here\n")
    resp = client.get(f"/api/tasks/{task_id}/files/content?path=secret.txt")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["binary"] is False
    assert "ghp_test" not in body["content"]
    assert "token" in body["content"]


def test_text_file_content(
    client: FlaskClient, session, app, config: Config, tmp_path
) -> None:
    task_id = _make_task(session)
    _seed_worktree(config, task_id, tmp_path / "outside")
    resp = client.get(f"/api/tasks/{task_id}/files/content?path=hello.txt")
    assert resp.status_code == 200
    assert resp.get_json()["content"] == "hello world\n"


def test_missing_task_returns_404(client: FlaskClient) -> None:
    assert client.get("/api/tasks/99999/files").status_code == 404


def test_no_worktree_returns_404(
    client: FlaskClient, session, app, config: Config
) -> None:
    task_id = _make_task(session)  # no worktree created
    resp = client.get(f"/api/tasks/{task_id}/files")
    assert resp.status_code == 404
    assert "no worktree" in resp.get_json()["error"].lower()
