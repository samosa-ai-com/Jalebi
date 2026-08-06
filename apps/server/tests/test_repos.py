from sqlalchemy.orm import Session as OrmSession

from jalebi import repos
from jalebi.db import Repo


def test_upsert_creates(session: OrmSession) -> None:
    row, created = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
    )
    assert created is True
    assert isinstance(row, Repo)
    assert row.default_branch == "main"


def test_upsert_updates_existing(session: OrmSession) -> None:
    repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
    )
    row, created = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="development",
        clone_url="https://github.com/owner/repo.git",
    )
    assert created is False
    assert row.default_branch == "development"
    assert len(repos.list_repos(session)) == 1


def test_list_repos_ordered(session: OrmSession) -> None:
    repos.upsert_repo(
        session, full_name="b/one", default_branch="main", clone_url="https://x/1.git"
    )
    repos.upsert_repo(
        session, full_name="a/two", default_branch="main", clone_url="https://x/2.git"
    )
    names = [r.full_name for r in repos.list_repos(session)]
    assert names == ["a/two", "b/one"]


def test_repo_to_dict(session: OrmSession) -> None:
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
    )
    data = repos.repo_to_dict(row)
    assert data["full_name"] == "owner/repo"
    assert data["default_branch"] == "main"
    assert data["webhook_registered"] is False
