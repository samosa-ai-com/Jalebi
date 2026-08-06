"""Repository registry: upsert and list connected repos."""

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
    """Insert or update a repo by ``full_name``. Returns ``(repo, created)``."""
    row = session.execute(select(Repo).where(Repo.full_name == full_name)).scalar_one_or_none()
    created = row is None
    if row is None:
        row = Repo(full_name=full_name, default_branch=default_branch, clone_url=clone_url)
        session.add(row)
    else:
        row.default_branch = default_branch
        row.clone_url = clone_url
    session.commit()
    return row, created


def list_repos(session: Session) -> list[Repo]:
    return list(session.execute(select(Repo).order_by(Repo.full_name)).scalars())


def repo_to_dict(repo: Repo) -> dict[str, object]:
    return {
        "id": repo.id,
        "full_name": repo.full_name,
        "default_branch": repo.default_branch,
        "clone_url": repo.clone_url,
        "webhook_registered": repo.webhook_registered,
        "poll_fallback": repo.poll_fallback,
        "check_runs_enabled": repo.check_runs_enabled,
        "last_checked_at": repo.last_checked_at.isoformat() if repo.last_checked_at else None,
    }
