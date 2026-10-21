"""Projects V2 board sync: webhook-first, PAT poll fallback (issue #23).

One central private board collects issues/PRs from every connected repo with
``project_sync_enabled`` — no per-repo workflow files (GitHub's native
Auto-add is capped at 1 repo on Free plans). Board ``Status`` automation
(``Item added -> Todo``, ``Closed/Merged -> Done``) stays native in the
Projects UI; Jalebi only adds items.

Design (kept simple per PRD Goal #10):
- One daemon thread (``ProjectSyncScheduler``) wakes every ``TICK_SECONDS``
  and syncs each due repo: due = sync-enabled, connected, never synced or
  older than ``PROJECT_SYNC_INTERVAL_SECONDS``. Repos with a working webhook
  (``webhook_registered`` and not ``poll_fallback``) are skipped — the webhook
  path already added their items ("webhook-first, poll fallback").
- The webhook path (``maybe_sync_delivery``, called from the webhook route
  next to ``nudger.on_webhook``) adds issues/PRs the moment the delivery
  arrives, using the payload's ``node_id`` directly (no extra API call).
  Failures are logged and never affect the webhook 200 response.
- Auth: listing uses the repo's bound account (``repos.pat_name`` — it can
  see the repo); board writes use the board account
  (``project_pat_name`` setting — it owns/can write the board). Both resolve
  strictly via ``secrets.resolve_token``: a missing account is an explicit
  error, never a silent fallback.
- Dedup: ``ProjectItem`` rows keyed by GitHub ``content_node_id`` (UNIQUE),
  so re-syncs and re-deliveries never duplicate items (the GraphQL mutation
  is idempotent as a second layer).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from jalebi import clock, masking, secrets, settings
from jalebi.config import Config
from jalebi.db import ProjectItem, Repo, now
from jalebi.github import GitHubClient, GitHubError

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
# Fixed poll interval — no dynamic setting (PRD Goal #10: webhooks carry the
# real-time load; the poller is only a backstop for missed deliveries and
# localhost installs without a public webhook URL).
PROJECT_SYNC_INTERVAL_SECONDS = 1800

SETTING_ENABLED = "project_sync_enabled"
SETTING_URL = "project_url"
SETTING_PAT = "project_pat_name"

# Webhook event.action pairs that carry a syncable issue/PR.
SYNC_ISSUE_ACTIONS = {"opened", "reopened", "transferred", "labeled"}
SYNC_PR_ACTIONS = {"opened", "reopened", "labeled", "ready_for_review"}


class ProjectSyncError(Exception):
    """Board sync is misconfigured or GitHub rejected the operation."""


def _masker_for(session: Session, config: Config):
    """Build the error-path masker (PATs + secret_patterns)."""
    raw_patterns = settings.get_setting(session, "secret_patterns") or []
    patterns = (
        [str(p) for p in raw_patterns if isinstance(p, str)]
        if isinstance(raw_patterns, list)
        else []
    )
    return masking.build_masker(secrets.all_token_values(config), patterns)


def board_config(session: Session, config: Config) -> dict[str, str] | None:
    """Return the board config, or None when sync is off/unconfigured.

    Requires the global switch, a board URL, and a known board account —
    anything less is "off", never a partial/guessed setup.
    """
    if not settings.get_setting(session, SETTING_ENABLED):
        return None
    url = settings.get_setting(session, SETTING_URL) or ""
    pat_name = settings.get_setting(session, SETTING_PAT) or ""
    if not (isinstance(url, str) and url.strip()):
        return None
    if not (isinstance(pat_name, str) and pat_name in secrets.token_names(config)):
        return None
    return {"url": url.strip(), "pat_name": pat_name}


class ProjectSyncEngine:
    """Adds issues/PRs to the central board; owns the node-id cache."""

    def __init__(self, config: Config):
        self.config = config
        # (board_url) -> project node id; keyed by URL so a config change
        # naturally misses the cache and re-resolves.
        self._project_ids: dict[str, str] = {}

    def _board_client(self, session: Session) -> tuple[GitHubClient, str]:
        """Board-authenticated client + board URL, or raise ``ProjectSyncError``."""
        cfg = board_config(session, self.config)
        if cfg is None:
            raise ProjectSyncError(
                "board sync is not configured: enable it in Settings "
                "(board URL + board account) first"
            )
        token = secrets.resolve_token(self.config, cfg["pat_name"])
        if token is None:
            raise ProjectSyncError(
                f"board account '{cfg['pat_name']}' has no stored token"
            )
        return GitHubClient(token), cfg["url"]

    def resolve_project_id(self, session: Session, client: GitHubClient, url: str) -> str:
        """Resolve (and cache) a board URL to its GraphQL node id."""
        if url not in self._project_ids:
            self._project_ids[url] = client.get_projectv2_id(url)
        return self._project_ids[url]

    def project_id(self, session: Session) -> str:
        """Resolve the configured board URL to its GraphQL node id."""
        client, url = self._board_client(session)
        try:
            return self.resolve_project_id(session, client, url)
        finally:
            client.close()

    def is_recorded(self, session: Session, repo_id: int, node_id: str) -> bool:
        """True when this issue/PR is already on the board."""
        return (
            session.execute(
                select(ProjectItem.id).where(
                    ProjectItem.repo_id == repo_id,
                    ProjectItem.content_node_id == node_id,
                )
            ).scalar_one_or_none()
            is not None
        )

    def mark_synced(
        self,
        session: Session,
        repo: Repo,
        content_type: str,
        number: int,
        node_id: str,
        item_id: str | None,
    ) -> bool:
        """Record an added item; False when a concurrent add won the race."""
        session.add(
            ProjectItem(
                repo_id=repo.id,
                content_type=content_type,
                content_node_id=node_id,
                item_number=number,
                project_item_id=item_id,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            # Webhook + poller raced on the same item: the other side already
            # recorded it — not an error.
            session.rollback()
            return False
        return True

    def add_item(
        self, session: Session, repo: Repo, content_type: str, number: int, node_id: str
    ) -> bool:
        """Add one issue/PR to the board; True when newly added.

        Already-recorded items are skipped without a GitHub call. GitHub
        failures propagate as ``GitHubError``/``ProjectSyncError`` for the
        caller to log (webhook) or count (scheduler).
        """
        if self.is_recorded(session, repo.id, node_id):
            return False
        project_id = self.project_id(session)
        client, _ = self._board_client(session)
        try:
            item_id = client.add_project_item(project_id, node_id)
        finally:
            client.close()
        # False only when a concurrent add recorded the watermark first — the
        # item is still on the board either way.
        return self.mark_synced(session, repo, content_type, number, node_id, item_id)

    def sync_repo(
        self, session: Session, repo: Repo, *, dry_run: bool = False
    ) -> dict[str, object]:
        """Add the repo's missing open issues/PRs to the board.

        Listing uses the repo's bound account; the board write uses the board
        account. ``dry_run`` reports what *would* be added without mutating
        GitHub or the watermark table — it works on repos that are not
        sync-enabled yet, as a pre-enable preview. ``project_last_sync_at``
        advances only when every add succeeded, so a transient failure is
        retried on the next run instead of being skipped forever.
        """
        if not repo.project_sync_enabled and not dry_run:
            raise ProjectSyncError(f"repo {repo.full_name} is not sync-enabled")
        repo_token = secrets.resolve_token(self.config, repo.pat_name)
        if repo_token is None:
            raise ProjectSyncError(
                f"repo {repo.full_name} has no stored token "
                f"(account '{repo.pat_name}')"
            )
        # Fail fast on board config before spending repo-list API calls.
        board_cfg = board_config(session, self.config)
        if board_cfg is None:
            raise ProjectSyncError("board sync is not configured")
        board_client, board_url = self._board_client(session)
        try:
            project_id = self.resolve_project_id(session, board_client, board_url)
            masker = _masker_for(session, self.config)

            # Wall-clock stamp -> offset-aware ISO: GitHub parses un-suffixed
            # timestamps as UTC, which would push `since` hours into the future
            # in zones ahead of UTC.
            since = (
                clock.to_iso(repo.project_last_sync_at)
                if repo.project_last_sync_at
                else None
            )
            repo_client = GitHubClient(repo_token)
            try:
                issues = repo_client.list_issues(repo.full_name, since=since)
                prs = repo_client.list_prs(repo.full_name)
            finally:
                repo_client.close()

            candidates = [("issue", i) for i in issues] + [("pr", p) for p in prs]
            candidates = [c for c in candidates if c[1].get("node_id")]
            missing = [
                c for c in candidates if not self.is_recorded(session, repo.id, c[1]["node_id"])
            ]
            if dry_run:
                return {
                    "repo": repo.full_name,
                    "dry_run": True,
                    "would_add": [
                        {"type": t, "number": item["number"]} for t, item in missing
                    ],
                }
            added = 0
            had_failure = False
            for content_type, item in missing:
                try:
                    item_id = board_client.add_project_item(
                        project_id, item["node_id"]
                    )
                except (GitHubError, httpx.HTTPError) as exc:
                    had_failure = True
                    logger.warning(
                        "board sync: skipping %s %s#%s: %s",
                        repo.full_name,
                        content_type,
                        item.get("number"),
                        masker(str(exc))[:500],
                    )
                    continue
                if self.mark_synced(
                    session, repo, content_type, item["number"], item["node_id"], item_id
                ):
                    added += 1
            # A failed item keeps its pre-run watermark so the next sync
            # re-lists and retries it instead of skipping it forever.
            if not had_failure:
                repo.project_last_sync_at = now()
            session.commit()
            return {"repo": repo.full_name, "dry_run": False, "added": added}
        finally:
            board_client.close()

    def maybe_sync_delivery(
        self, session: Session, repo: Repo, event: str, payload: dict
    ) -> bool:
        """Webhook path: add the delivered issue/PR to the board.

        Returns True when the item is on the board afterwards (newly added or
        already recorded). Returns False (never raises) when sync is off, the
        event is irrelevant, or GitHub rejected the add — the webhook response
        must always stay 200.
        """
        try:
            if not repo.project_sync_enabled:
                return False
            if board_config(session, self.config) is None:
                return False
            action = payload.get("action")
            node_id: str | None = None
            number: int | None = None
            content_type: str | None = None
            if event == "issues" and action in SYNC_ISSUE_ACTIONS:
                issue = payload.get("issue") or {}
                node_id, number, content_type = (
                    issue.get("node_id"),
                    issue.get("number"),
                    "issue",
                )
            elif event == "pull_request" and action in SYNC_PR_ACTIONS:
                pr = payload.get("pull_request") or {}
                node_id, number, content_type = (
                    pr.get("node_id"),
                    pr.get("number"),
                    "pr",
                )
            if not (node_id and number and content_type):
                return False
            if self.is_recorded(session, repo.id, node_id):
                return True
            try:
                added = self.add_item(session, repo, content_type, number, node_id)
            except (GitHubError, ProjectSyncError, SQLAlchemyError) as exc:
                # Roll back first: a failed transaction poisons this request's
                # session, and the route still needs it for rule matching and
                # delivery bookkeeping below.
                session.rollback()
                logger.warning(
                    "board sync: webhook add failed for %s #%s: %s",
                    repo.full_name,
                    number,
                    _masker_for(session, self.config)(str(exc))[:500],
                )
                return False
            if not added:
                # Lost a race with the scheduler after the fast-path check:
                # re-check instead of reporting a failure.
                return self.is_recorded(session, repo.id, node_id)
            return True
        except Exception:  # noqa: BLE001 - webhook must always answer 200
            logger.exception("board sync: unexpected webhook failure")
            return False


class ProjectSyncScheduler:
    """Daemon-thread poll fallback: syncs due repos every tick."""

    def __init__(self, config: Config):
        self.config = config
        self.engine = ProjectSyncEngine(config)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="jalebi-project-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a tick must never kill the loop
                logger.exception("project sync tick failed")

    def tick(self, now_dt: datetime | None = None) -> int:
        """Sync every due repo once. Returns the count of repos synced."""
        from jalebi import db  # local import avoids a cycle at module load

        session = db.Session()
        try:
            if board_config(session, self.config) is None:
                return 0
            current = now_dt or now()
            synced = 0
            for repo in (
                session.execute(select(Repo).where(Repo.connected.is_(True)))
                .scalars()
                .all()
            ):
                if not repo.project_sync_enabled:
                    continue
                # No webhook shortcut: the scheduler is the backfill guarantee
                # (initial sync, missed deliveries during downtime). Webhook
                # deliveries skip here naturally once `project_last_sync_at`
                # is fresh — at most one cheap `since`-filtered sweep per
                # interval per repo.
                if (
                    repo.project_last_sync_at is not None
                    and (current - repo.project_last_sync_at).total_seconds()
                    < PROJECT_SYNC_INTERVAL_SECONDS
                ):
                    continue
                try:
                    self.engine.sync_repo(session, repo)
                    synced += 1
                except (
                    GitHubError,
                    ProjectSyncError,
                    httpx.HTTPError,
                    SQLAlchemyError,
                ) as exc:
                    # Roll back first: without it the session stays aborted and
                    # every later repo in this tick fails, and the masker query
                    # below would raise PendingRollbackError itself.
                    session.rollback()
                    try:
                        masker = _masker_for(session, self.config)
                        logger.warning(
                            "board sync: %s failed: %s", repo.full_name, masker(str(exc))[:500]
                        )
                    except Exception:  # noqa: BLE001 - best-effort logging
                        logger.exception("board sync: logging failed")
            return synced
        finally:
            session.close()
