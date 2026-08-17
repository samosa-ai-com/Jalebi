"""Phase 4 T2 — PR polling observer + derived attention status (T2.1/T2.2)."""


import httpx
import pytest

from jalebi import secrets
from jalebi.config import Config
from jalebi.github import (
    PR_REVIEWS_PATH,
    PR_STATUS_PATH,
    PRS_PATH,
    GitHubUnauthorized,
)
from jalebi.poller import Poller, _normalize_ci_state, _review_decision_from_reviews


@pytest.fixture(autouse=True)
def _seed_test_account(config, monkeypatch):
    """Every account is a named account — seed ``test`` for the poller fixture."""
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.add_github_token(config, "test", "ghp_test")


# ---- Poller unit tests ------------------------------------------------------


class _RecordingGitHubClient:
    """Test double implementing the poller's GitHubClient surface.

    Records every call (path, etag arg) and returns canned bodies + new
    ETags. Routes the three poller methods through the same ``etag``
    contract as the production client — a 304 returns ``(empty_body,
    etag, True)``.
    """

    def __init__(
        self,
        *,
        open_prs: list[dict] | None = None,
        statuses: dict[str, dict] | None = None,
        reviews: dict[int, list[dict]] | None = None,
        raise_exc: Exception | None = None,
        raise_401: bool = False,
    ):
        self.open_prs = open_prs or []
        self.statuses = statuses or {}
        self.reviews = reviews or {}
        self.raise_exc = raise_exc
        self.raise_401 = raise_401
        self.calls: list[tuple[str, str | None]] = []
        self._etag_counter = 0

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"W/\"etag-{self._etag_counter}\""

    def list_open_prs(self, full_name: str, *, etag: str | None = None):
        if self.raise_401:
            raise GitHubUnauthorized("HTTP 401")
        if self.raise_exc:
            raise self.raise_exc
        self.calls.append((PRS_PATH.format(full_name=full_name), etag))
        # First call returns new data + new etag; subsequent call with the
        # matching etag returns 304.
        if etag and etag.startswith("W/\"etag-") and self.open_prs:
            return [], etag, True
        return list(self.open_prs), self._next_etag(), False

    def list_check_runs_for_ref(self, full_name: str, ref: str, *, etag=None):
        if self.raise_401:
            raise GitHubUnauthorized("HTTP 401")
        if self.raise_exc:
            raise self.raise_exc
        path = PR_STATUS_PATH.format(full_name=full_name, ref=ref)
        self.calls.append((path, etag))
        if etag and etag.startswith("W/\"etag-"):
            return {"state": None, "total_count": 0, "statuses": []}, etag, True
        body = self.statuses.get(ref, {"state": None, "total_count": 0, "statuses": []})
        return body, self._next_etag(), False

    def list_reviews_for_pr(self, full_name: str, pr_number: int, *, etag=None):
        if self.raise_401:
            raise GitHubUnauthorized("HTTP 401")
        if self.raise_exc:
            raise self.raise_exc
        path = PR_REVIEWS_PATH.format(full_name=full_name, pr_number=pr_number)
        self.calls.append((path, etag))
        if etag and etag.startswith("W/\"etag-"):
            return [], etag, True
        return self.reviews.get(pr_number, []), self._next_etag(), False

    def close(self) -> None:  # pragma: no cover - parity with real client
        pass


@pytest.fixture
def fake_repo(session):
    """A connected repo with ``poll_fallback=True`` (the only safe default
    for poller tests). Caller can flip the flag off when needed."""
    from jalebi import repos

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://example.invalid/owner/repo.git",
        pat_name="test",
    )
    row.poll_fallback = True
    row.connected = True
    session.commit()
    return row


def _install_fake_client(monkeypatch, fake: _RecordingGitHubClient) -> None:
    """Wire the fake client where ``Poller._poll_repo`` looks for it."""
    monkeypatch.setattr("jalebi.poller.GitHubClient", lambda token: fake)


# ---- pure helpers -----------------------------------------------------------


def test_normalize_ci_state_maps_error_to_failure() -> None:
    assert _normalize_ci_state("success") == "success"
    assert _normalize_ci_state("failure") == "failure"
    assert _normalize_ci_state("pending") == "pending"
    assert _normalize_ci_state("error") == "failure"
    assert _normalize_ci_state(None) is None


def test_review_decision_prioritizes_changes_requested_over_approval() -> None:
    reviews = [
        {"state": "APPROVED"},
        {"state": "CHANGES_REQUESTED"},
        {"state": "APPROVED"},
    ]
    assert _review_decision_from_reviews(reviews) == "changes_requested"


def test_review_decision_approved_when_no_changes_requested() -> None:
    reviews = [{"state": "APPROVED"}, {"state": "COMMENTED"}]
    assert _review_decision_from_reviews(reviews) == "approved"


def test_review_decision_review_required_when_only_comments() -> None:
    reviews = [{"state": "COMMENTED"}]
    assert _review_decision_from_reviews(reviews) == "review_required"


def test_review_decision_none_when_no_reviews() -> None:
    assert _review_decision_from_reviews([]) is None


def test_review_decision_ignores_dismissed_and_pending() -> None:
    reviews = [{"state": "DISMISSED"}, {"state": "PENDING"}]
    assert _review_decision_from_reviews(reviews) is None


# ---- tick behavior ----------------------------------------------------------


def test_tick_noop_when_flag_off(
    config: Config, session, monkeypatch
) -> None:
    fake = _RecordingGitHubClient(open_prs=[])
    _install_fake_client(monkeypatch, fake)
    from jalebi import repos

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/off",
        default_branch="main",
        clone_url="https://example.invalid/owner/off.git",
        pat_name="test",
    )
    row.poll_fallback = False  # explicit
    session.commit()

    poller = Poller(config)
    assert poller.tick() == 0
    assert fake.calls == []  # never touched the client
    assert poller.pr_facts_for_task(row.id, 1) is None


def test_tick_polls_and_builds_facts(
    config: Config, session, fake_repo, monkeypatch
) -> None:
    fake = _RecordingGitHubClient(
        open_prs=[
            {"number": 3, "head": "jalebi/7", "head_sha": "abc123", "mergeable": True}
        ],
        statuses={"abc123": {"state": "success", "total_count": 1, "statuses": []}},
        reviews={3: [{"state": "APPROVED", "submitted_at": "2026-08-17T00:00:00Z"}]},
    )
    _install_fake_client(monkeypatch, fake)

    poller = Poller(config)
    assert poller.tick() == 1

    facts = poller.pr_facts_for_task(fake_repo.id, 7)
    assert facts is not None
    assert facts["ci_state"] == "success"
    assert facts["review_decision"] == "approved"
    assert facts["mergeable"] is True
    assert "last_seen_at" in facts

    # Repo timestamp updated.
    session.refresh(fake_repo)
    assert fake_repo.last_checked_at is not None


def test_tick_skips_non_jalebi_branches(
    config: Config, session, fake_repo, monkeypatch
) -> None:
    fake = _RecordingGitHubClient(
        open_prs=[{"number": 5, "head": "feature/x", "head_sha": "sha", "mergeable": False}],
    )
    _install_fake_client(monkeypatch, fake)
    poller = Poller(config)
    assert poller.tick() == 1
    assert poller.pr_facts_for_task(fake_repo.id, 99) is None


def test_tick_skips_malformed_task_branch(
    config: Config, session, fake_repo, monkeypatch
) -> None:
    fake = _RecordingGitHubClient(
        open_prs=[{"number": 6, "head": "jalebi/abc", "head_sha": "sha", "mergeable": False}],
    )
    _install_fake_client(monkeypatch, fake)
    poller = Poller(config)
    # Tick succeeds (last_checked_at updated); no facts.
    assert poller.tick() == 1
    assert poller.pr_facts_for_task(fake_repo.id, 1) is None


def test_tick_pauses_on_httpx_error(
    config: Config, session, fake_repo, monkeypatch
) -> None:
    fake = _RecordingGitHubClient(raise_exc=httpx.ConnectError("boom"))
    _install_fake_client(monkeypatch, fake)
    poller = Poller(config)
    # Tick returns 0; last_checked_at unchanged.
    assert poller.tick() == 0
    session.refresh(fake_repo)
    assert fake_repo.last_checked_at is None


def test_401_drops_repo_facts_and_etags(
    config: Config, session, fake_repo, monkeypatch
) -> None:
    fake = _RecordingGitHubClient(
        open_prs=[
            {"number": 1, "head": "jalebi/9", "head_sha": "x", "mergeable": True}
        ],
    )
    _install_fake_client(monkeypatch, fake)
    poller = Poller(config)
    assert poller.tick() == 1
    assert poller.pr_facts_for_task(fake_repo.id, 9) is not None

    # Second tick raises 401.
    fake.raise_401 = True
    assert poller.tick() == 0
    # Facts for the repo dropped; per-task lookup now returns None.
    assert poller.pr_facts_for_task(fake_repo.id, 9) is None


def test_etag_reuse_and_304_keeps_payload(
    config: Config, session, fake_repo, monkeypatch
) -> None:
    """First tick stores an ETag; second tick reuses it; on 304 the poller
    keeps the cached facts (no per-PR fan-out)."""
    fake = _RecordingGitHubClient(
        open_prs=[
            {"number": 4, "head": "jalebi/11", "head_sha": "s", "mergeable": True}
        ],
        statuses={"s": {"state": "success", "total_count": 1, "statuses": []}},
        reviews={4: [{"state": "APPROVED"}]},
    )
    _install_fake_client(monkeypatch, fake)
    poller = Poller(config)
    poller.tick()
    poller.tick()
    # The PR-list call reused the ETag: first tick had no prior etag (None);
    # the fake returned 'W/"etag-1"' which the poller stored; the second
    # tick's call must carry it. Filter to PR-list calls only.
    pr_list_url = PRS_PATH.format(full_name="owner/repo")
    pr_list_calls = [c for c in fake.calls if c[0] == pr_list_url]
    assert len(pr_list_calls) == 2
    assert pr_list_calls[0][1] is None  # first tick: no prior etag
    assert pr_list_calls[1][1] == 'W/"etag-1"'  # second tick: etag reused
    # Facts survived the 304.
    facts = poller.pr_facts_for_task(fake_repo.id, 11)
    assert facts is not None
    assert facts["ci_state"] == "success"


def test_stale_pr_pruned(
    config: Config, session, fake_repo, monkeypatch
) -> None:
    fake = _RecordingGitHubClient(
        open_prs=[
            {"number": 3, "head": "jalebi/7", "head_sha": "s", "mergeable": True}
        ],
    )
    _install_fake_client(monkeypatch, fake)
    poller = Poller(config)
    poller.tick()
    assert poller.pr_facts_for_task(fake_repo.id, 7) is not None

    # PR closed → next tick has no PRs.
    fake.open_prs = []
    poller.tick()
    assert poller.pr_facts_for_task(fake_repo.id, 7) is None


def test_flag_off_prunes_facts(
    config: Config, session, fake_repo, monkeypatch
) -> None:
    fake = _RecordingGitHubClient(
        open_prs=[{"number": 1, "head": "jalebi/5", "head_sha": "s", "mergeable": True}],
    )
    _install_fake_client(monkeypatch, fake)
    poller = Poller(config)
    poller.tick()
    assert poller.pr_facts_for_task(fake_repo.id, 5) is not None

    # Flip the flag off + tick — facts pruned.
    fake_repo.poll_fallback = False
    session.commit()
    fake.calls.clear()
    poller.tick()
    assert fake.calls == []  # no client calls (the pruned repo isn't polled)
    assert poller.pr_facts_for_task(fake_repo.id, 5) is None


def test_etag_cache_capped(config: Config) -> None:
    """Insert 513 distinct URLs; oldest is evicted (FIFO)."""
    poller = Poller(config)
    for i in range(513):
        poller._put_etag(f"/repos/o/r/{i}", f"W/\"e-{i}\"")
    assert len(poller._etags) == 512
    # Earliest (i=0) should be evicted; the most recent (i=512) should remain.
    assert "/repos/o/r/0" not in poller._etags
    assert poller._etags["/repos/o/r/512"] == 'W/"e-512"'


# ---- thread lifecycle -------------------------------------------------------


def test_poller_thread_start_stop_clean(config: Config) -> None:
    poller = Poller(config)
    poller.start()
    assert poller._thread is not None and poller._thread.is_alive()
    poller.stop()
    poller.join(timeout=2)
    assert not poller._thread.is_alive()


def test_poller_double_start_is_safe(config: Config) -> None:
    poller = Poller(config)
    poller.start()
    first = poller._thread
    poller.start()  # no-op; should not spawn a second one
    assert poller._thread is first
    poller.stop()
    poller.join(timeout=2)
