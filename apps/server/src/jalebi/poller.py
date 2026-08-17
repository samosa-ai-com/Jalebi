"""PR/CI/review polling observer (Phase 4 T2.1).

A daemon thread that ticks every 30 s for every connected repo with
``poll_fallback=True``. For each open PR whose head branch starts with
``jalebi/``, fetches combined commit-status + review state (PAT-compatible —
check-runs are GitHub-App-only) and stores a normalized ``PRFacts`` per
``(repo_id, pr_number)``. ``routes/tasks.py`` consumes the facts via
``pr_facts_for_task`` to derive ``attention_for``.

Default OFF — no repo sets ``poll_fallback=True`` out of the box. A repo
whose ``poll_fallback`` flips off (or which has been disconnected) is
pruned on the next tick (no stale state). A 401 (rotated PAT) drops that
repo's facts + etags so the next tick re-warms cleanly.

Mirrors ``ScreeningScheduler`` (``screening.py``) for thread/loop/lock shape.
"""

import logging
import threading
from datetime import datetime
from typing import cast

import httpx
from sqlalchemy.orm import Session

from jalebi import clock, db, repos, secrets
from jalebi.attention import PRFacts
from jalebi.config import Config
from jalebi.db import Repo
from jalebi.db import now as _now
from jalebi.github import (
    PR_REVIEWS_PATH,
    PR_STATUS_PATH,
    PRS_PATH,
    GitHubClient,
    GitHubError,
    GitHubUnauthorized,
)

logger = logging.getLogger(__name__)


TICK_SECONDS = 30
ETAG_CACHE_CAP = 512

_BRANCH_PREFIX = "jalebi/"
_APPROVED = "APPROVED"
_CHANGES_REQUESTED = "CHANGES_REQUESTED"
_COMMENTED = "COMMENTED"


def _normalize_ci_state(state: str | None) -> str | None:
    """Map GitHub's combined-status ``state`` to our attention vocabulary.

    ``error`` → ``"failure"`` (it can mean a check setup failed, which we
    surface as red). ``None`` / ``"pending"`` pass through. ``success``
    means green. An empty ``total_count`` (no statuses at all) returns
    ``None`` — different from ``"success"``, which means "all green".
    """
    if state == "error":
        return "failure"
    return state


def _review_decision_from_reviews(reviews: list[dict]) -> str | None:
    """Final review decision for a PR (T2.1).

    Only final states count: ``APPROVED`` / ``CHANGES_REQUESTED`` /
    ``COMMENTED``. ``DISMISSED`` / ``PENDING`` are ignored. Any
    ``CHANGES_REQUESTED`` wins; otherwise any ``APPROVED`` wins; otherwise
    the PR still needs reviews (``"review_required"``); ``None`` when no
    reviews at all.
    """
    states = [r.get("state") for r in reviews]
    if _CHANGES_REQUESTED in states:
        return "changes_requested"
    if _APPROVED in states:
        return "approved"
    if any(s in (_APPROVED, _CHANGES_REQUESTED, _COMMENTED) for s in states):
        return "review_required"
    return None


class Poller:
    """Daemon thread that polls open-PR state for ``poll_fallback=True`` repos.

    Thread/loop pattern mirrors ``ScreeningScheduler``. The poller is
    instantiated by ``create_app`` (``JALEBI_POLLER`` key) and started in
    ``main()``. Tests instantiate it directly without starting the thread
    so they can call ``tick()`` synchronously.
    """

    def __init__(self, config: Config):
        self.config = config
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # (repo_id, pr_number) -> PRFacts
        self._facts: dict[tuple[int, int], PRFacts] = {}
        # (repo_id, task_id) -> pr_number (so routes resolve by task_id)
        self._task_pr: dict[tuple[int, int], int] = {}
        # URL path -> ETag (FIFO eviction via dict insertion order)
        self._etags: dict[str, str] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="jalebi-poller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a tick must never kill the loop
                logger.exception("poller tick failed")

    # -- facts access (consumed by routes) -----------------------------------

    def pr_facts_for_task(self, repo_id: int, task_id: int) -> PRFacts | None:
        """Return a COPY of the PRFacts for ``task_id`` (or None when no PR)."""
        with self._lock:
            pr_number = self._task_pr.get((repo_id, task_id))
            if pr_number is None:
                return None
            facts = self._facts.get((repo_id, pr_number))
        return cast(PRFacts, dict(facts)) if facts is not None else None

    def record_facts(
        self, repo_id: int, task_id: int, pr_number: int, facts: PRFacts
    ) -> None:
        """Inject pre-built facts (used by tests + the T2.1 tick itself)."""
        with self._lock:
            self._facts[(repo_id, pr_number)] = cast(PRFacts, dict(facts))
            self._task_pr[(repo_id, task_id)] = pr_number

    # -- tick ----------------------------------------------------------------

    def tick(self) -> int:
        """Run one poll cycle. Returns the number of repos successfully polled.

        On any repo whose ``poll_fallback`` is now False (or which has been
        disconnected) the per-repo facts/etags are pruned — covers the
        "stale facts after OFF" risk from the plan §8.
        """
        session = db.Session()
        polled: set[int] = set()
        success = 0
        try:
            connected = list(repos.list_repos(session))
            for repo in connected:
                if not repo.connected or not repo.poll_fallback:
                    self._prune_repo(repo.id, repo.full_name)
                    continue
                polled.add(repo.id)
                try:
                    if self._poll_repo(session, repo):
                        success += 1
                except (httpx.HTTPError, GitHubError) as exc:
                    logger.warning(
                        "poller skip repo %s: %s", repo.full_name, exc
                    )
            # Prune anything we never polled this tick (flag flipped off
            # between iterations, etc.).
            for repo in connected:
                if repo.id not in polled:
                    self._prune_repo(repo.id, repo.full_name)
            session.commit()
        finally:
            session.close()
        return success

    # -- internals -----------------------------------------------------------

    def _put_etag(self, url: str, etag: str | None) -> None:
        if not etag:
            return
        with self._lock:
            if len(self._etags) >= ETAG_CACHE_CAP:
                # FIFO eviction: dict preserves insertion order; pop the
                # oldest entry.
                self._etags.pop(next(iter(self._etags)), None)
            self._etags[url] = etag

    def _prune_repo(self, repo_id: int, full_name: str) -> None:
        """Drop facts, task→PR index, and etags for ``full_name``.

        Repo-scoped because every poller URL begins with
        ``/repos/{full_name}/``; an unambiguous prefix match.
        """
        prefix = f"/repos/{full_name}/"
        with self._lock:
            self._facts = {
                k: v for k, v in self._facts.items() if k[0] != repo_id
            }
            self._task_pr = {
                k: v for k, v in self._task_pr.items() if k[0] != repo_id
            }
            self._etags = {
                k: v for k, v in self._etags.items() if not k.startswith(prefix)
            }

    def _poll_repo(self, session: Session, repo: Repo) -> bool:
        token = secrets.resolve_token(self.config, repo.pat_name)
        if token is None:
            logger.warning("poller skip repo %s: no token", repo.full_name)
            return False
        client = GitHubClient(token)
        try:
            pulls_path = PRS_PATH.format(full_name=repo.full_name)
            with self._lock:
                existing_etag = self._etags.get(pulls_path)
            prs, new_etag, not_modified = client.list_open_prs(
                repo.full_name, etag=existing_etag
            )
            self._put_etag(pulls_path, new_etag)

            open_pr_numbers: set[int] = set()
            if not not_modified:
                for pr in prs:
                    number = pr.get("number")
                    head = pr.get("head") or ""
                    head_sha = pr.get("head_sha")
                    mergeable = pr.get("mergeable")
                    if not isinstance(number, int):
                        continue
                    if not head.startswith(_BRANCH_PREFIX):
                        continue
                    try:
                        task_id = int(head[len(_BRANCH_PREFIX):])
                    except ValueError:
                        logger.debug(
                            "poller skip PR #%s: malformed head branch %r",
                            number,
                            head,
                        )
                        continue
                    open_pr_numbers.add(number)
                    self._update_pr(
                        client,
                        repo.full_name,
                        repo.id,
                        task_id,
                        number,
                        head_sha,
                        mergeable,
                    )

                # Prune facts for PRs that are no longer open (only when we
                # had a fresh PR list — on a 304 the list is unchanged so we
                # trust the cached facts and skip the prune).
                with self._lock:
                    stale = [
                        k
                        for k in self._facts
                        if k[0] == repo.id and k[1] not in open_pr_numbers
                    ]
                    for k in stale:
                        self._facts.pop(k, None)
                        self._task_pr = {
                            tk: tv
                            for tk, tv in self._task_pr.items()
                            if not (tk[0] == repo.id and tv == k[1])
                        }

            repo.last_checked_at = _now()
            return True
        except GitHubUnauthorized:
            # Token revoked/rotated; drop this repo's state and let the
            # next tick re-warm from scratch (the route's PATCH will
            # re-set the flag if the owner wants to keep polling).
            logger.warning(
                "poller repo %s returned 401; dropping cached state",
                repo.full_name,
            )
            self._prune_repo(repo.id, repo.full_name)
            return False
        finally:
            client.close()

    def _update_pr(
        self,
        client: GitHubClient,
        full_name: str,
        repo_id: int,
        task_id: int,
        pr_number: int,
        head_sha: str | None,
        mergeable: bool | None,
    ) -> None:
        """Fetch + persist facts for a single PR (T2.1 step 6).

        ``mergeable`` comes from the freshly-listed PR (always re-fetched
        when the PR list isn't 304); ``ci_state`` and ``review_decision``
        come from per-endpoint calls that respect ETag independently.
        """
        prev = self._pr_facts_snapshot(repo_id, pr_number)

        ci_state: str | None
        review_decision: str | None

        # CI (commit-status; PAT-compatible).
        if head_sha:
            status_path = PR_STATUS_PATH.format(
                full_name=full_name, ref=head_sha
            )
            with self._lock:
                existing = self._etags.get(status_path)
            status_body, status_etag, status_304 = client.list_check_runs_for_ref(
                full_name, head_sha, etag=existing
            )
            self._put_etag(status_path, status_etag)
            if status_304:
                ci_state = prev["ci_state"] if prev else None
            else:
                ci_state = (
                    _normalize_ci_state(status_body.get("state"))
                    if status_body.get("total_count", 0) > 0
                    else None
                )
        else:
            ci_state = prev["ci_state"] if prev else None

        # Reviews.
        reviews_path = PR_REVIEWS_PATH.format(
            full_name=full_name, pr_number=pr_number
        )
        with self._lock:
            existing = self._etags.get(reviews_path)
        reviews, rev_etag, rev_304 = client.list_reviews_for_pr(
            full_name, pr_number, etag=existing
        )
        self._put_etag(reviews_path, rev_etag)
        if rev_304:
            review_decision = prev["review_decision"] if prev else None
        else:
            review_decision = _review_decision_from_reviews(reviews)

        facts: PRFacts = {
            "ci_state": ci_state,
            "review_decision": review_decision,
            "mergeable": mergeable,
            "last_seen_at": clock.to_iso(datetime.now()),
        }
        self.record_facts(repo_id, task_id, pr_number, facts)

    def _pr_facts_snapshot(self, repo_id: int, pr_number: int) -> PRFacts | None:
        with self._lock:
            facts = self._facts.get((repo_id, pr_number))
        return cast(PRFacts, dict(facts)) if facts else None


# Re-export the symbols that older tests imported via this module.
__all__ = [
    "Poller",
    "TICK_SECONDS",
    "ETAG_CACHE_CAP",
    "_normalize_ci_state",
    "_review_decision_from_reviews",
]
