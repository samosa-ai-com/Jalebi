"""Projects V2 board sync engine + scheduler (issue #23).

All GitHub access is faked by monkeypatching ``jalebi.project_sync.GitHubClient``.
No live API calls, no real PATs (``ghp_test`` fixtures only).
"""

import pytest

from jalebi import repos, secrets, settings
from jalebi.db import ProjectItem
from jalebi.github import GitHubError
from jalebi.project_sync import (
    ProjectSyncEngine,
    ProjectSyncError,
    ProjectSyncScheduler,
    board_config,
)


@pytest.fixture(autouse=True)
def _tokens(app):
    cfg = app.config["JALEBI_CONFIG"]
    secrets.add_github_token(cfg, "board-acct", "ghp_board")
    secrets.add_github_token(cfg, "repo-acct", "ghp_repo")
    yield
    secrets.remove_github_token(cfg, "board-acct")
    secrets.remove_github_token(cfg, "repo-acct")


@pytest.fixture()
def enabled_board(session):
    settings.set_setting(session, "project_sync_enabled", True)
    settings.set_setting(session, "project_url", "https://github.com/orgs/octo-org/projects/5")
    settings.set_setting(session, "project_pat_name", "board-acct")


@pytest.fixture()
def repo_row(session):
    repo, _ = repos.upsert_repo(
        session,
        full_name="owner/sync-repo",
        default_branch="main",
        clone_url="https://example.com/owner/sync-repo.git",
        pat_name="repo-acct",
    )
    session.commit()
    return repo


class FakeBoardClient:
    """Fake for BOTH the board client and the repo-list client."""

    instances: list["FakeBoardClient"] = []
    issues: list[dict] = []
    prs: list[dict] = []
    fail_add: bool = False

    def __init__(self, token: str):
        self.token = token
        self.add_calls: list[tuple[str, str]] = []
        FakeBoardClient.instances.append(self)

    def close(self) -> None:
        pass

    def get_projectv2_id(self, url: str) -> str:
        assert url == "https://github.com/orgs/octo-org/projects/5"
        return "PVT_board"

    def add_project_item(self, project_id: str, node_id: str) -> str:
        self.add_calls.append((project_id, node_id))
        if FakeBoardClient.fail_add:
            raise GitHubError("GraphQL error: forbidden")
        return f"PVTI_{node_id}"

    def list_issues(self, full_name: str, since: str | None = None):
        assert full_name == "owner/sync-repo"
        return list(FakeBoardClient.issues)

    def list_prs(self, full_name: str):
        assert full_name == "owner/sync-repo"
        return list(FakeBoardClient.prs)


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch):
    FakeBoardClient.instances.clear()
    FakeBoardClient.issues = []
    FakeBoardClient.prs = []
    FakeBoardClient.fail_add = False
    monkeypatch.setattr("jalebi.project_sync.GitHubClient", FakeBoardClient)


@pytest.fixture()
def engine(app):
    return ProjectSyncEngine(app.config["JALEBI_CONFIG"])


def _enable_repo(session, repo_row):
    repo_row.project_sync_enabled = True
    session.commit()


# -- board_config -----------------------------------------------------


def test_board_config_none_when_disabled(session, app):
    assert board_config(session, app.config["JALEBI_CONFIG"]) is None


def test_board_config_none_when_unknown_account(session, app, enabled_board):
    settings.set_setting(session, "project_pat_name", "nope")
    assert board_config(session, app.config["JALEBI_CONFIG"]) is None


def test_board_config_ok(session, app, enabled_board):
    cfg = board_config(session, app.config["JALEBI_CONFIG"])
    assert cfg == {
        "url": "https://github.com/orgs/octo-org/projects/5",
        "pat_name": "board-acct",
    }


# -- sync_repo --------------------------------------------------------


def test_sync_repo_dry_run_adds_nothing(session, app, enabled_board, repo_row, engine):
    _enable_repo(session, repo_row)
    FakeBoardClient.issues = [{"number": 1, "node_id": "I_1"}]
    FakeBoardClient.prs = [{"number": 2, "node_id": "PR_2"}]
    out = engine.sync_repo(session, repo_row, dry_run=True)
    assert out == {
        "repo": "owner/sync-repo",
        "dry_run": True,
        "would_add": [{"type": "issue", "number": 1}, {"type": "pr", "number": 2}],
    }
    assert all(not c.add_calls for c in FakeBoardClient.instances)
    assert session.query(ProjectItem).count() == 0
    assert repo_row.project_last_sync_at is None


def test_sync_repo_adds_and_dedups(session, app, enabled_board, repo_row, engine):
    _enable_repo(session, repo_row)
    FakeBoardClient.issues = [{"number": 1, "node_id": "I_1"}]
    out = engine.sync_repo(session, repo_row)
    assert out == {"repo": "owner/sync-repo", "dry_run": False, "added": 1}
    assert repo_row.project_last_sync_at is not None
    # Second run: already recorded, zero adds, zero GitHub mutations.
    for c in FakeBoardClient.instances:
        c.add_calls.clear()
    out2 = engine.sync_repo(session, repo_row)
    assert out2["added"] == 0
    assert all(not c.add_calls for c in FakeBoardClient.instances)


def test_sync_repo_items_without_node_id_skipped(
    session, app, enabled_board, repo_row, engine
):
    _enable_repo(session, repo_row)
    FakeBoardClient.issues = [{"number": 1, "node_id": None}]
    out = engine.sync_repo(session, repo_row)
    assert out["added"] == 0


def test_sync_repo_refuses_disabled_repo(session, app, enabled_board, repo_row, engine):
    with pytest.raises(ProjectSyncError, match="not sync-enabled"):
        engine.sync_repo(session, repo_row)


def test_sync_repo_dry_run_works_on_disabled_repo(
    session, app, enabled_board, repo_row, engine
):
    # Dry-run is the pre-enable preview: allowed without the per-repo opt-in.
    FakeBoardClient.issues = [{"number": 1, "node_id": "I_1"}]
    out = engine.sync_repo(session, repo_row, dry_run=True)
    assert out["would_add"] == [{"type": "issue", "number": 1}]
    assert session.query(ProjectItem).count() == 0


def test_sync_repo_failure_keeps_watermark_for_retry(
    session, app, enabled_board, repo_row, engine
):
    _enable_repo(session, repo_row)
    FakeBoardClient.issues = [{"number": 1, "node_id": "I_1"}]
    FakeBoardClient.fail_add = True
    out = engine.sync_repo(session, repo_row)
    assert out["added"] == 0
    # Watermark NOT advanced: the retry run must see the item again.
    assert repo_row.project_last_sync_at is None
    FakeBoardClient.fail_add = False
    out = engine.sync_repo(session, repo_row)
    assert out["added"] == 1
    assert repo_row.project_last_sync_at is not None


def test_sync_repo_refuses_missing_repo_token(
    session, app, enabled_board, repo_row, engine
):
    _enable_repo(session, repo_row)
    repo_row.pat_name = "ghost"
    session.commit()
    with pytest.raises(ProjectSyncError, match="no stored token"):
        engine.sync_repo(session, repo_row)


def test_sync_repo_refuses_unconfigured_board(session, app, repo_row, engine):
    _enable_repo(session, repo_row)
    with pytest.raises(ProjectSyncError, match="not configured"):
        engine.sync_repo(session, repo_row)


def test_sync_repo_add_failure_skips_item(
    session, app, enabled_board, repo_row, engine
):
    _enable_repo(session, repo_row)
    FakeBoardClient.issues = [
        {"number": 1, "node_id": "I_1"},
        {"number": 2, "node_id": "I_2"},
    ]
    FakeBoardClient.fail_add = True
    out = engine.sync_repo(session, repo_row)
    assert out["added"] == 0
    assert session.query(ProjectItem).count() == 0


# -- maybe_sync_delivery (webhook path) --------------------------------


def test_webhook_adds_issue_on_opened(session, app, enabled_board, repo_row, engine):
    _enable_repo(session, repo_row)
    payload = {"action": "opened", "issue": {"number": 7, "node_id": "I_7"}}
    assert engine.maybe_sync_delivery(session, repo_row, "issues", payload) is True
    assert engine.is_recorded(session, repo_row.id, "I_7")


def test_webhook_ignores_irrelevant_action(session, app, enabled_board, repo_row, engine):
    _enable_repo(session, repo_row)
    payload = {"action": "closed", "issue": {"number": 7, "node_id": "I_7"}}
    assert engine.maybe_sync_delivery(session, repo_row, "issues", payload) is False
    assert not engine.is_recorded(session, repo_row.id, "I_7")


def test_webhook_pr_ready_for_review(session, app, enabled_board, repo_row, engine):
    _enable_repo(session, repo_row)
    payload = {
        "action": "ready_for_review",
        "pull_request": {"number": 9, "node_id": "PR_9"},
    }
    assert engine.maybe_sync_delivery(session, repo_row, "pull_request", payload) is True


def test_webhook_returns_true_when_already_recorded(
    session, app, enabled_board, repo_row, engine
):
    _enable_repo(session, repo_row)
    engine.mark_synced(session, repo_row, "issue", 7, "I_7", "PVTI_I_7")
    payload = {"action": "opened", "issue": {"number": 7, "node_id": "I_7"}}
    assert engine.maybe_sync_delivery(session, repo_row, "issues", payload) is True
    assert all(not c.add_calls for c in FakeBoardClient.instances)


def test_webhook_failure_isolated(session, app, enabled_board, repo_row, engine):
    _enable_repo(session, repo_row)
    FakeBoardClient.fail_add = True
    payload = {"action": "opened", "issue": {"number": 7, "node_id": "I_7"}}
    assert engine.maybe_sync_delivery(session, repo_row, "issues", payload) is False
    assert not engine.is_recorded(session, repo_row.id, "I_7")


def test_webhook_off_when_repo_not_enabled(session, app, enabled_board, repo_row, engine):
    payload = {"action": "opened", "issue": {"number": 7, "node_id": "I_7"}}
    assert engine.maybe_sync_delivery(session, repo_row, "issues", payload) is False


# -- scheduler tick ----------------------------------------------------


def test_tick_syncs_webhook_healthy_repos_as_backfill(
    session, app, enabled_board, repo_row
):
    # The scheduler is the backfill guarantee (initial sync, missed
    # deliveries): webhook-healthy repos are NOT skipped, only governed by the
    # interval watermark.
    repo_row.project_sync_enabled = True
    repo_row.webhook_registered = True
    repo_row.poll_fallback = False
    session.commit()
    FakeBoardClient.issues = [{"number": 1, "node_id": "I_1"}]
    sched = ProjectSyncScheduler(app.config["JALEBI_CONFIG"])
    assert sched.tick() == 1
    assert session.query(ProjectItem).count() == 1


def test_tick_skips_recently_synced_repos(session, app, enabled_board, repo_row):
    repo_row.project_sync_enabled = True
    session.commit()
    FakeBoardClient.issues = [{"number": 1, "node_id": "I_1"}]
    sched = ProjectSyncScheduler(app.config["JALEBI_CONFIG"])
    assert sched.tick() == 1
    # Immediate re-tick: interval not elapsed → skipped.
    assert sched.tick() == 0


def test_tick_syncs_due_repos(session, app, enabled_board, repo_row):
    repo_row.project_sync_enabled = True
    session.commit()
    FakeBoardClient.issues = [{"number": 1, "node_id": "I_1"}]
    sched = ProjectSyncScheduler(app.config["JALEBI_CONFIG"])
    assert sched.tick() == 1
    assert session.query(ProjectItem).count() == 1
    # Immediate re-tick: interval not elapsed → skipped.
    assert sched.tick() == 0


def test_tick_off_when_board_unconfigured(session, app, repo_row):
    repo_row.project_sync_enabled = True
    session.commit()
    sched = ProjectSyncScheduler(app.config["JALEBI_CONFIG"])
    assert sched.tick() == 0
