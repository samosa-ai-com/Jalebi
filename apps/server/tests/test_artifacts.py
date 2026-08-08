"""Artifact capture + retention tests (PRD F18)."""

import json
import subprocess

import pytest
from sqlalchemy import text

from jalebi import artifacts, repos, secrets, settings, tasks
from jalebi.adapters.types import AgentEvent
from jalebi.db import Artifact, Run, utcnow
from jalebi.git_workspace import GitWorkspace

FULL_NAME = "owner/repo"


def _git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class FakeProc:
    def poll(self):
        return None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def wait(self, timeout=None) -> int:
        return 0


class FakeHandle:
    def __init__(self, events):
        self._events = list(events)
        self.session_id = "ses_fake"
        self.proc = FakeProc()

    def events(self):
        yield from self._events


@pytest.fixture(autouse=True)
def _fake_token(config, monkeypatch):
    secrets.add_github_token(config, "test", "ghp_test")


@pytest.fixture
def git_remote(tmp_path) -> str:
    remote = tmp_path / "remote.git"
    src = tmp_path / "src"
    _git(["init", "--bare", str(remote)])
    _git(["init", str(src)])
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    (src / "tracked.txt").write_text("tracked\n")
    _git(["-C", str(src), "add", "tracked.txt"])
    _git(["-C", str(src), "commit", "-m", "initial"])
    _git(["-C", str(src), "branch", "-M", "main"])
    _git(["-C", str(src), "remote", "add", "origin", str(remote)])
    _git(["-C", str(src), "push", "-u", "origin", "main"])
    _git(["-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"])
    return str(remote)


@pytest.fixture
def repo_row(session, git_remote):
    row, _ = repos.upsert_repo(
        session,
        full_name=FULL_NAME,
        default_branch="main",
        clone_url=git_remote,
        pat_name="test",
    )
    return row


@pytest.fixture
def q(app):
    return app.config["JALEBI_QUEUE"]


def _install_adapter(monkeypatch, handle) -> None:
    class FakeAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            return handle

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: FakeAdapter())


# -- capture --------------------------------------------------------------


def test_capture_untracked_files_only(session, repo_row, tmp_path) -> None:
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="p")
    worktree = tmp_path / "wt"
    _git(["init", str(worktree)])
    (worktree / "tracked.txt").write_text("a\n")
    (worktree / ".gitignore").write_text("node_modules/\n")
    _git(["-C", str(worktree), "config", "user.email", "t@example.com"])
    _git(["-C", str(worktree), "config", "user.name", "Test"])
    _git(["-C", str(worktree), "add", "tracked.txt", ".gitignore"])
    _git(["-C", str(worktree), "commit", "-m", "init"])

    (worktree / "report.log").write_text("done\n")
    (worktree / "out").mkdir()
    (worktree / "out" / "shot.png").write_bytes(b"\x89PNG")
    (worktree / "node_modules").mkdir()
    (worktree / "node_modules" / "x.js").write_text("ignored\n")

    run = Run(task_id=task.id, seq=1, status="done", started_at=utcnow())
    session.add(run)
    session.commit()

    captured, skipped = artifacts.capture_run_artifacts(session, run, worktree, tmp_path)
    assert skipped == []
    assert sorted(str(c["path"]) for c in captured) == ["out/shot.png", "report.log"]

    session.expire_all()
    rows = session.query(Artifact).filter_by(run_id=run.id).order_by(Artifact.id).all()
    assert sorted(r.path for r in rows) == ["out/shot.png", "report.log"]
    by_path = {r.path: r.size for r in rows}
    assert by_path["report.log"] == len("done\n")

    store = artifacts.artifact_store_dir(tmp_path) / str(run.id)
    assert (store / "report.log").read_text() == "done\n"
    assert (store / "out" / "shot.png").read_bytes() == b"\x89PNG"
    assert not (store / "tracked.txt").exists()


def test_prune_removes_expired_artifacts(session, repo_row, tmp_path) -> None:
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="p")
    run = Run(task_id=task.id, seq=1, status="done", started_at=utcnow())
    session.add(run)
    session.flush()

    store = artifacts.artifact_store_dir(tmp_path) / str(run.id)
    store.mkdir(parents=True)
    (store / "old.log").write_text("old")
    (store / "new.log").write_text("new")

    old = Artifact(run_id=run.id, path="old.log", size=3, created_at=utcnow())
    new = Artifact(run_id=run.id, path="new.log", size=3, created_at=utcnow())
    session.add_all([old, new])
    session.flush()
    session.execute(
        text(
            "UPDATE artifacts SET created_at = datetime('now', '-30 days') WHERE path = 'old.log'"
        )
    )
    session.commit()

    removed = artifacts.prune_artifacts(session, tmp_path, ttl_days=7)
    assert removed == 1
    session.expire_all()
    remaining = session.query(Artifact).all()
    assert [r.path for r in remaining] == ["new.log"]
    assert not (store / "old.log").exists()
    assert (store / "new.log").exists()


def test_artifact_file_rejects_traversal(tmp_path) -> None:
    with pytest.raises(ValueError):
        artifacts.artifact_file(tmp_path, 1, "../../etc/passwd")


# -- queue integration ----------------------------------------------------


def test_run_captures_artifacts_from_worktree(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    wt = git.create_worktree(task.id, FULL_NAME, "main")
    (wt / "agent-output.txt").write_text("hello from agent\n")

    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    q._run_task(task.id)

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"
    run = tasks.latest_run(session, task.id)
    assert run is not None
    assert json.loads(run.artifacts_json or "[]") == [
        {"path": "agent-output.txt", "size": len("hello from agent\n")}
    ]
    rows = tasks.list_artifacts(session, run.id)
    assert [r.path for r in rows] == ["agent-output.txt"]
    store_file = artifacts.artifact_file(q.config.data_dir, run.id, "agent-output.txt")
    assert store_file.read_text() == "hello from agent\n"


def test_download_artifact_endpoint(app, session, repo_row) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    run = Run(task_id=task.id, seq=1, status="done", started_at=utcnow())
    session.add(run)
    session.commit()

    config = app.config["JALEBI_CONFIG"]
    store = artifacts.artifact_store_dir(config.data_dir) / str(run.id)
    store.mkdir(parents=True)
    (store / "out.log").write_text("content")
    artifact_row = Artifact(run_id=run.id, path="out.log", size=7)
    session.add(artifact_row)
    session.commit()

    client = app.test_client()
    resp = client.get(f"/api/tasks/{task.id}/artifacts/{artifact_row.id}/download")
    assert resp.status_code == 200
    assert resp.data == b"content"
    assert "attachment" in resp.headers.get("Content-Disposition", "")

    assert (
        client.get(f"/api/tasks/{task.id}/artifacts/9999/download").status_code == 404
    )


def test_run_dict_includes_artifacts(app, session, repo_row) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    run = Run(task_id=task.id, seq=1, status="done", started_at=utcnow())
    session.add(run)
    session.flush()
    session.add(Artifact(run_id=run.id, path="a.txt", size=3))
    session.commit()

    client = app.test_client()
    detail = client.get(f"/api/tasks/{task.id}").get_json()
    arts = detail["run"]["artifacts"]
    assert len(arts) == 1
    assert arts[0]["path"] == "a.txt"
    assert arts[0]["size"] == 3


def test_capture_excludes_jalebi_internal(tmp_path, session) -> None:
    """After bootstrap, .jalebi/ files are ignored and never captured."""
    from jalebi import worktree_bootstrap

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-q", str(repo)])
    _git(["-C", str(repo), "config", "user.email", "t@example.com"])
    _git(["-C", str(repo), "config", "user.name", "Test"])
    (repo / "base.txt").write_text("x\n")
    _git(["-C", str(repo), "add", "base.txt"])
    _git(["-C", str(repo), "commit", "-m", "init"])

    repo_row, _ = repos.upsert_repo(
        session,
        full_name=FULL_NAME,
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="x")
    worktree_bootstrap.bootstrap_worktree(repo)
    (repo / "output.log").write_text("logs\n")
    (repo / ".jalebi").mkdir(exist_ok=True)
    (repo / ".jalebi" / "pr.md").write_text("# title\n")

    run = Run(task_id=task.id, seq=1, status="done", started_at=utcnow(), finished_at=utcnow())
    session.add(run)
    session.flush()

    captured, _skipped = artifacts.capture_run_artifacts(session, run, repo, tmp_path)
    paths = [str(c["path"]) for c in captured]
    assert "output.log" in paths
    assert not any(".jalebi" in p for p in paths)
    assert ".jalebi/pr.md" not in paths
    # bootstrap files are excluded from artifacts too (info/exclude + backstop set)
    assert "AGENTS.md" not in paths
    assert "opencode.json" not in paths


def test_capture_masks_text_files(session, repo_row, tmp_path) -> None:
    from jalebi.masking import build_masker

    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="p")
    run = Run(task_id=task.id, seq=1, status="done", started_at=utcnow())
    session.add(run)
    session.commit()

    worktree = tmp_path / "wt"
    _git(["init", str(worktree)])
    _git(["-C", str(worktree), "config", "user.email", "t@example.com"])
    _git(["-C", str(worktree), "config", "user.name", "Test"])
    (worktree / "base.txt").write_text("base\n")
    _git(["-C", str(worktree), "add", "."])
    _git(["-C", str(worktree), "commit", "-m", "init"])
    (worktree / "note.txt").write_text("token ghp_leak here\n")
    (worktree / "pkg.bin").write_bytes(b"\xff\xfeghp_leak\x00\x01")

    masker = build_masker("ghp_leak", [])
    captured, skipped = artifacts.capture_run_artifacts(
        session, run, worktree, tmp_path, masker=masker, secret_values=["ghp_leak"]
    )
    assert [str(c["path"]) for c in captured] == ["note.txt"]
    assert skipped == ["pkg.bin"]  # binary containing the token → dropped

    store = artifacts.artifact_store_dir(tmp_path) / str(run.id)
    assert (store / "note.txt").read_text() == "token *** here\n"
    assert not (store / "pkg.bin").exists()


def test_capture_skips_oversized_files(session, repo_row, tmp_path, monkeypatch) -> None:
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="p")
    run = Run(task_id=task.id, seq=1, status="done", started_at=utcnow())
    session.add(run)
    session.commit()

    worktree = tmp_path / "wt"
    _git(["init", str(worktree)])
    _git(["-C", str(worktree), "config", "user.email", "t@example.com"])
    _git(["-C", str(worktree), "config", "user.name", "Test"])
    (worktree / "base.txt").write_text("base\n")
    _git(["-C", str(worktree), "add", "."])
    _git(["-C", str(worktree), "commit", "-m", "init"])
    (worktree / "big.bin").write_bytes(b"x" * (artifacts.ARTIFACT_MAX_BYTES + 1))
    (worktree / "ok.txt").write_text("fine\n")

    captured, skipped = artifacts.capture_run_artifacts(session, run, worktree, tmp_path)
    assert [str(c["path"]) for c in captured] == ["ok.txt"]
    assert skipped == ["big.bin"]
