"""Phase 4 T1.6 — per-run git SHA stamping.

End-to-end: a seeded run through ``tasks.run_to_dict`` exposes ``git_sha_start``
and ``git_sha_end`` (with the ``-dirty`` suffix when the worktree is unclean).
The SHA itself comes from the queue's ``_stamp_git_sha`` helper, so we exercise
both the helper (unit-style, against a real git workspace) and the serialization
(against a directly-mutated Run row).
"""


from jalebi import tasks as tasks_svc
from jalebi.config import Config
from jalebi.git_workspace import GitWorkspace


def _seed_dirty_worktree(ws: GitWorkspace, tmp_path, *, with_file: bool = True):
    """Return (worktree, head_sha) for a freshly-created worktree on jalebi/1.

    When ``with_file`` is True, leave an untracked file behind so the
    working tree is dirty.
    """
    import subprocess as _sp
    from pathlib import Path

    remote = tmp_path / "remote.git"
    src = tmp_path / "src"
    _sp.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    _sp.run(["git", "init", str(src)], check=True, capture_output=True)
    _sp.run(["git", "-C", str(src), "config", "user.email", "t@example.com"], check=True)
    _sp.run(["git", "-C", str(src), "config", "user.name", "Test"], check=True)
    (src / "file.txt").write_text("hello\n")
    _sp.run(["git", "-C", str(src), "add", "file.txt"], check=True)
    _sp.run(["git", "-C", str(src), "commit", "-m", "initial"], check=True)
    _sp.run(["git", "-C", str(src), "branch", "-M", "main"], check=True)
    _sp.run(["git", "-C", str(src), "remote", "add", "origin", str(remote)], check=True)
    _sp.run(["git", "-C", str(src), "push", "-u", "origin", "main"], check=True)
    _sp.run(["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)

    ws.ensure_mirror("owner/repo", str(remote))
    wt = ws.create_worktree(1, "owner/repo", "main")
    head = ws.rev_parse_head(wt)
    if with_file:
        (Path(wt) / "uncommitted.txt").write_text("dirty\n")
    return wt, head


def test_stamp_git_sha_clean_returns_just_sha(
    config: Config, tmp_path, monkeypatch
) -> None:
    """A clean worktree stamps just the 40-hex SHA (no `-dirty` suffix)."""
    ws = GitWorkspace(config)
    # Use a clean tmp path (no uncommitted file).
    wt, head = _seed_dirty_worktree(ws, tmp_path, with_file=False)
    # We can't easily call queue._stamp_git_sha without a Queue; use the same
    # logic via the helpers.
    from pathlib import Path


    try:
        # Reuse the queue helper indirectly via a Queue instance bound to config.
        from jalebi.queue import TaskQueue

        q = TaskQueue.__new__(TaskQueue)
        q.config = config
        sha = q._stamp_git_sha(ws, Path(wt))
    finally:
        pass
    assert sha == head
    assert sha is not None
    assert not sha.endswith("-dirty")


def test_stamp_git_sha_dirty_appends_suffix(config: Config, tmp_path) -> None:
    """An untracked file in the worktree forces the `-dirty` suffix."""
    ws = GitWorkspace(config)
    wt, head = _seed_dirty_worktree(ws, tmp_path, with_file=True)
    from pathlib import Path

    from jalebi.queue import TaskQueue

    q = TaskQueue.__new__(TaskQueue)
    q.config = config
    sha = q._stamp_git_sha(ws, Path(wt))
    assert sha == f"{head}-dirty"


def test_run_dict_exposes_git_sha_fields() -> None:
    """``tasks.run_to_dict`` includes both SHA fields; None-safe when unset."""
    from jalebi.db import Run

    run = Run(id=99, task_id=1, seq=1, status="done", git_sha_start="abc123", git_sha_end="def456")
    d = tasks_svc.run_to_dict(run)
    assert d["git_sha_start"] == "abc123"
    assert d["git_sha_end"] == "def456"

    empty = Run(id=100, task_id=1, seq=1, status="done")
    d2 = tasks_svc.run_to_dict(empty)
    assert d2["git_sha_start"] is None
    assert d2["git_sha_end"] is None
