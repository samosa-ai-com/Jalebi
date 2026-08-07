"""Artifact capture from task worktrees + retention (PRD F18).

After a run completes, files the agent produced but did not commit — untracked,
non-ignored files — are copied from the worktree into
``<data-dir>/artifacts/<run_id>/`` and recorded in the ``artifacts`` table (with
``runs.artifacts_json`` kept as a cache of refs).
"""

import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi.db import Artifact, utcnow


def artifact_store_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "artifacts"


def _untracked_files(worktree: Path) -> list[str]:
    """Relative paths of untracked, non-ignored files in ``worktree``.

    Jalebi's own bootstrap files (``AGENTS.md``, ``opencode.json``,
    ``.gitignore``) are excluded — they are infrastructure, not agent output.
    ``.jalebi/*`` is also ignored via the worktree ``.gitignore`` (e.g.
    ``pr.md``, ``review.md``), so it never shows up as an artifact.
    """
    proc = subprocess.run(
        ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    excluded = {"AGENTS.md", "opencode.json", ".gitignore"}
    return [p for p in proc.stdout.split("\0") if p and p not in excluded]


def capture_run_artifacts(
    session: Session, run, worktree: Path, data_dir: Path
) -> list[dict[str, object]]:
    """Copy untracked worktree files into the store and record ``Artifact`` rows."""
    store = artifact_store_dir(data_dir) / str(run.id)
    captured: list[dict[str, object]] = []
    for rel in _untracked_files(worktree):
        src = worktree / rel
        if not src.is_file():
            continue
        dst = store / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        size = src.stat().st_size
        session.add(Artifact(run_id=run.id, path=rel, size=size))
        captured.append({"path": rel, "size": size})
    if captured:
        session.flush()
    return captured


def prune_artifacts(session: Session, data_dir: Path, ttl_days: int) -> int:
    """Delete artifact rows (and stored files) older than ``ttl_days``; returns count."""
    if ttl_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=ttl_days)
    rows = list(
        session.execute(select(Artifact).where(Artifact.created_at < cutoff)).scalars()
    )
    store = artifact_store_dir(data_dir)
    removed = 0
    for row in rows:
        path = store / str(row.run_id) / row.path
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        session.delete(row)
        removed += 1
    if removed:
        session.commit()
    return removed


def artifact_file(data_dir: Path, run_id: int, relpath: str) -> Path:
    """Resolve an artifact's stored file, refusing path traversal."""
    base = (artifact_store_dir(data_dir) / str(run_id)).resolve()
    candidate = (base / relpath).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError("invalid artifact path")
    return candidate
