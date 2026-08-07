"""Artifact capture from task worktrees + retention (PRD F18).

After a run completes, files the agent produced but did not commit — untracked,
non-ignored files — are copied from the worktree into
``<data-dir>/artifacts/<run_id>/`` and recorded in the ``artifacts`` table (with
``runs.artifacts_json`` kept as a cache of refs).

Capture applies the ingest masker (PRD F17): text files are masked before being
stored, binary files are dropped if they contain any known secret value, and a
per-file size cap bounds a runaway agent. Files dropped this way are returned as
``skipped`` so the caller can surface a note.
"""

import subprocess
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi.db import Artifact, utcnow

ARTIFACT_MAX_BYTES = 10 * 1024 * 1024


def artifact_store_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "artifacts"


def _untracked_files(worktree: Path) -> list[str]:
    """Relative paths of untracked, non-ignored files in ``worktree``.

    Jalebi's bootstrap files (``AGENTS.md``, ``opencode.json``) and ``.jalebi/*``
    are excluded via the mirror's shared ``info/exclude`` (see
    ``worktree_bootstrap``), so they never appear as artifacts. The explicit set
    below is a belt-and-braces backstop in case the exclude is missing.
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
    session: Session,
    run,
    worktree: Path,
    data_dir: Path,
    *,
    masker: Callable[[str], str] | None = None,
    secret_values: list[str] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    """Copy untracked worktree files into the store; return ``(captured, skipped)``.

    Text files are run through ``masker`` before being written. Binary files (and
    oversized files) are checked against ``secret_values`` and skipped entirely if
    any known value appears in their bytes — the agent env holds the PAT, so a
    dumped response could otherwise be served unmasked (PRD F17).
    """
    store = artifact_store_dir(data_dir) / str(run.id)
    captured: list[dict[str, object]] = []
    skipped: list[str] = []
    for rel in _untracked_files(worktree):
        src = worktree / rel
        if not src.is_file():
            continue
        size = src.stat().st_size
        if size > ARTIFACT_MAX_BYTES:
            skipped.append(rel)
            continue
        data = src.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # Binary file: keep it unless it contains a known secret value.
            if any(secret and secret.encode() in data for secret in (secret_values or ())):
                skipped.append(rel)
                continue
        else:
            if masker is not None:
                text = masker(text)
            data = text.encode("utf-8")
        dst = store / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        size = len(data)
        session.add(Artifact(run_id=run.id, path=rel, size=size))
        captured.append({"path": rel, "size": size})
    if captured:
        session.flush()
    return captured, skipped


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
