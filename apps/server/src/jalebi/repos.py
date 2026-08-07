"""Repository registry: upsert and list connected repos.

A repo row is never hard-deleted while tasks reference it (FK). Disconnecting is
a **soft** operation: ``repos.connected`` is flipped to false, hiding it from the
UI pickers and the repos list while preserving task history. Reconnecting (via
the GitHub page) simply flips it back.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi.db import Repo


def upsert_repo(
    session: Session,
    *,
    full_name: str,
    default_branch: str,
    clone_url: str,
) -> tuple[Repo, bool]:
    """Insert or (re)connect a repo by ``full_name``. Returns ``(repo, created)``."""
    row = session.execute(select(Repo).where(Repo.full_name == full_name)).scalar_one_or_none()
    created = row is None
    if row is None:
        row = Repo(full_name=full_name, default_branch=default_branch, clone_url=clone_url)
        session.add(row)
    else:
        row.default_branch = default_branch
        row.clone_url = clone_url
        row.connected = True  # reconnect
    session.commit()
    return row, created


def list_repos(session: Session, connected_only: bool = True) -> list[Repo]:
    query = select(Repo).order_by(Repo.full_name)
    if connected_only:
        query = query.where(Repo.connected.is_(True))
    return list(session.execute(query).scalars())


def repo_to_dict(repo: Repo) -> dict[str, object]:
    return {
        "id": repo.id,
        "full_name": repo.full_name,
        "default_branch": repo.default_branch,
        "clone_url": repo.clone_url,
        "connected": repo.connected,
        "webhook_registered": repo.webhook_registered,
        "poll_fallback": repo.poll_fallback,
        "check_runs_enabled": repo.check_runs_enabled,
        "last_checked_at": repo.last_checked_at.isoformat() if repo.last_checked_at else None,
    }
