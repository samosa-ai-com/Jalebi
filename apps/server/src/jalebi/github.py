"""Thin GitHub REST client (httpx) with PAT scope validation."""

import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

API_BASE_URL = "https://api.github.com"
DEFAULT_TIMEOUT = 10.0
MAX_PAGINATION_PAGES = 10  # bound Link-header following (10×per_page)

REQUIRED_CLASSIC_SCOPES = ("repo",)

FINE_GRAINED_NOTE = (
    "fine-grained token: GitHub does not expose an enumerable scope list. "
    "Verify Contents (RW), Pull requests (RW), Issues (RW), Metadata (read), "
    "and Commit statuses (RW) in the GitHub UI."
)

TokenType = Literal["classic", "fine-grained", "unknown"]


class GitHubError(Exception):
    """Raised when the GitHub API returns a non-success response."""


class GitHubNotFound(GitHubError):
    """Raised when a requested resource does not exist (HTTP 404)."""


@dataclass
class TokenInfo:
    valid: bool
    login: str | None = None
    token_type: TokenType | None = None
    granted_scopes: list[str] = field(default_factory=list)
    missing_scopes: list[str] = field(default_factory=list)
    note: str | None = None
    error: str | None = None


class GitHubClient:
    def __init__(self, token: str, base_url: str = API_BASE_URL, timeout: float = DEFAULT_TIMEOUT):
        self._http = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Jalebi",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs) -> tuple[int, Any, dict[str, str]]:
        """Issue a request; returns ``(status_code, json_body, headers)``."""
        resp = self._http.request(method, path, **kwargs)
        try:
            body = resp.json()
        except ValueError:
            body = None
        return resp.status_code, body, dict(resp.headers)

    @staticmethod
    def _next_page(headers: dict[str, str]) -> int | None:
        """Page number from a GitHub ``Link: <…>; rel="next"`` header, or None."""
        link = headers.get("link")
        if not link:
            return None
        for part in link.split(","):
            segment, _, rel = part.partition(";")
            if 'rel="next"' in rel:
                url = segment.strip().strip("<>")
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                try:
                    return int(query["page"][0])
                except (KeyError, ValueError):
                    return None
        return None

    def _request_paginated(self, path: str, params: dict[str, Any]) -> list[Any]:
        """GET ``path`` following ``Link rel=next`` until exhausted (capped)."""
        items: list[Any] = []
        page = 1
        for _ in range(MAX_PAGINATION_PAGES):
            current = dict(params, page=page)
            status, body, headers = self._request("GET", path, params=current)
            if status != 200 or not isinstance(body, list):
                raise GitHubError(f"failed to list {path}: HTTP {status}")
            items.extend(body)
            next_page = self._next_page(headers)
            if next_page is None:
                break
            page = next_page
        return items

    def validate_token(self) -> TokenInfo:
        """Validate the PAT against ``GET /user`` and enumerate classic scopes."""
        status, body, headers = self._request("GET", "/user")
        if status != 200:
            message = "authentication failed"
            if isinstance(body, dict) and body.get("message"):
                message = body["message"]
            return TokenInfo(valid=False, error=message)

        login = body.get("login") if isinstance(body, dict) else None

        if "x-oauth-scopes" in headers:
            scopes = [s.strip() for s in headers["x-oauth-scopes"].split(",") if s.strip()]
            missing = [s for s in REQUIRED_CLASSIC_SCOPES if s not in scopes]
            return TokenInfo(
                valid=not missing,
                login=login,
                token_type="classic",
                granted_scopes=scopes,
                missing_scopes=missing,
            )

        return TokenInfo(valid=True, login=login, token_type="fine-grained", note=FINE_GRAINED_NOTE)

    def get_repo(self, full_name: str) -> dict[str, Any]:
        """Fetch a single repository's info by ``owner/repo``."""
        status, body, _ = self._request("GET", f"/repos/{full_name}")
        if status == 404:
            raise GitHubNotFound(full_name)
        if status != 200 or not isinstance(body, dict):
            raise GitHubError(f"failed to fetch repo: HTTP {status}")
        return {
            "full_name": body.get("full_name"),
            "default_branch": body.get("default_branch"),
            "clone_url": body.get("clone_url"),
            "private": body.get("private"),
        }

    def create_pr(
        self,
        full_name: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> int:
        """Open a pull request and return its number."""
        status, payload, _ = self._request(
            "POST",
            f"/repos/{full_name}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        )
        if status not in (200, 201) or not isinstance(payload, dict) or "number" not in payload:
            raise GitHubError(f"failed to create PR: HTTP {status}")
        return payload["number"]

    def find_pr_by_head(self, full_name: str, head: str) -> int | None:
        """Return the number of an existing **open** PR whose head ref is ``head``.

        ``head`` is matched same-repo as ``{owner}:{head}`` (cross-repo PRs from
        forks are intentionally ignored so an agent-created fork PR can never
        be mistaken for the task's own PR). Closed/merged PRs are never matched:
        a stale PR reusing a ``jalebi/<task_id>`` branch name must not short-
        circuit publishing a fresh run.
        """
        owner = full_name.split("/", 1)[0]
        status, body, _ = self._request(
            "GET",
            f"/repos/{full_name}/pulls",
            params={"state": "open", "head": f"{owner}:{head}", "per_page": 100},
        )
        if status != 200 or not isinstance(body, list):
            raise GitHubError(f"failed to list PRs: HTTP {status}")
        for pr in body:
            if (
                isinstance(pr, dict)
                and pr.get("state") == "open"
                and pr.get("head", {}).get("ref") == head
            ):
                return pr.get("number")
        return None

    def list_issues(self, full_name: str, state: str = "open") -> list[dict[str, Any]]:
        """List the repo's issues (PRs excluded by GitHub's issue API)."""
        body = self._request_paginated(
            f"/repos/{full_name}/issues", {"state": state, "per_page": 100}
        )
        return [
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "html_url": issue.get("html_url"),
                "state": issue.get("state"),
                "pull_request": "pull_request" in issue,  # issues API includes PRs
            }
            for issue in body
            if "pull_request" not in issue
        ]

    def get_issue(self, full_name: str, number: int) -> dict[str, Any]:
        status, body, _ = self._request("GET", f"/repos/{full_name}/issues/{number}")
        if status == 404:
            raise GitHubNotFound(f"{full_name}#{number}")
        if status != 200 or not isinstance(body, dict):
            raise GitHubError(f"failed to fetch issue: HTTP {status}")
        if "pull_request" in body:
            raise GitHubError(f"{full_name}#{number} is a pull request, not an issue")
        return {
            "number": body.get("number"),
            "title": body.get("title"),
            "body": body.get("body") or "",
            "html_url": body.get("html_url"),
            "state": body.get("state"),
        }

    def comment_on_issue(self, full_name: str, number: int, body: str) -> None:
        status, _, _ = self._request(
            "POST", f"/repos/{full_name}/issues/{number}/comments", json={"body": body}
        )
        if status != 201:
            raise GitHubError(f"failed to comment on issue #{number}: HTTP {status}")

    def list_prs(self, full_name: str, state: str = "open") -> list[dict[str, Any]]:
        body = self._request_paginated(
            f"/repos/{full_name}/pulls", {"state": state, "per_page": 100}
        )
        return [
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "html_url": pr.get("html_url"),
                "state": pr.get("state"),
                "base": (pr.get("base") or {}).get("ref"),
                "head": (pr.get("head") or {}).get("ref"),
                "author": (pr.get("user") or {}).get("login"),
            }
            for pr in body
        ]

    def get_pr(self, full_name: str, number: int) -> dict[str, Any]:
        status, body, _ = self._request("GET", f"/repos/{full_name}/pulls/{number}")
        if status == 404:
            raise GitHubNotFound(f"{full_name}#{number}")
        if status != 200 or not isinstance(body, dict):
            raise GitHubError(f"failed to fetch PR: HTTP {status}")
        return {
            "number": body.get("number"),
            "title": body.get("title"),
            "body": body.get("body") or "",
            "html_url": body.get("html_url"),
            "state": body.get("state"),
            "base": (body.get("base") or {}).get("ref"),
            "head": (body.get("head") or {}).get("ref"),
            "author": (body.get("user") or {}).get("login"),
        }

    def post_pr_review(self, full_name: str, pr_number: int, body: str) -> None:
        """Post a PR review comment (event COMMENT) — never approves/merges."""
        status, _, _ = self._request(
            "POST",
            f"/repos/{full_name}/pulls/{pr_number}/reviews",
            json={"event": "COMMENT", "body": body},
        )
        if status not in (200, 201):
            raise GitHubError(f"failed to post review on PR #{pr_number}: HTTP {status}")

    def list_branches(self, full_name: str) -> list[str]:
        """List the repo's branch names (paged)."""
        body = self._request_paginated(f"/repos/{full_name}/branches", {"per_page": 100})
        return [name for name in (b.get("name") for b in body) if isinstance(name, str)]

    def list_repos(self, per_page: int = 100) -> list[dict[str, Any]]:
        body = self._request_paginated(
            "/user/repos",
            {"per_page": per_page, "sort": "updated", "visibility": "all"},
        )
        return [
            {
                "full_name": repo.get("full_name"),
                "private": repo.get("private"),
                "default_branch": repo.get("default_branch"),
                "clone_url": repo.get("clone_url"),
                "html_url": repo.get("html_url"),
            }
            for repo in body
        ]
