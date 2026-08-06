"""Thin GitHub REST client (httpx) with PAT scope validation."""

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

API_BASE_URL = "https://api.github.com"
DEFAULT_TIMEOUT = 10.0

REQUIRED_CLASSIC_SCOPES = ("repo",)

FINE_GRAINED_NOTE = (
    "fine-grained token: GitHub does not expose an enumerable scope list. "
    "Verify Contents (RW), Pull requests (RW), Issues (RW), Metadata (read), "
    "and Commit statuses (RW) in the GitHub UI."
)

TokenType = Literal["classic", "fine-grained", "unknown"]


class GitHubError(Exception):
    """Raised when the GitHub API returns a non-success response."""


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

    def list_repos(self, per_page: int = 100) -> list[dict[str, Any]]:
        """List the authenticated user's repositories (name, default branch, clone URL)."""
        status, body, _ = self._request(
            "GET",
            "/user/repos",
            params={"per_page": per_page, "sort": "updated", "visibility": "all"},
        )
        if status != 200 or not isinstance(body, list):
            raise GitHubError(f"failed to list repos: HTTP {status}")
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
