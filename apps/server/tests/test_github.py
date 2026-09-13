import time
from email.utils import formatdate

import pytest

from jalebi.github import (
    RATE_LIMIT_MAX_RETRIES,
    RATE_LIMIT_MAX_WAIT,
    GitHubClient,
    GitHubError,
    GitHubNotFound,
)


def make_client(token: str = "ghp_test") -> GitHubClient:
    return GitHubClient(token)


def test_validate_classic_full_scopes(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kw: (
            200,
            {"login": "octocat"},
            {"x-oauth-scopes": "repo, workflow"},
        ),
    )
    info = client.validate_token()
    assert info.valid is True
    assert info.login == "octocat"
    assert info.token_type == "classic"
    assert info.granted_scopes == ["repo", "workflow"]
    assert info.missing_scopes == []
    assert info.has_workflow is True


def test_validate_classic_missing_repo(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kw: (200, {"login": "octocat"}, {"x-oauth-scopes": "public_repo"}),
    )
    info = client.validate_token()
    assert info.valid is False
    assert info.token_type == "classic"
    assert info.granted_scopes == ["public_repo"]
    assert info.missing_scopes == ["repo"]


def test_validate_classic_repo_without_workflow(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kw: (
            200,
            {"login": "octocat"},
            {"x-oauth-scopes": "repo"},
        ),
    )
    info = client.validate_token()
    assert info.valid is True
    assert info.token_type == "classic"
    assert info.has_workflow is False


def test_validate_fine_grained_no_scope_header(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kw: (200, {"login": "octocat"}, {}),
    )
    info = client.validate_token()
    assert info.valid is True
    assert info.token_type == "fine-grained"
    assert info.granted_scopes == []
    assert info.has_workflow is None
    assert "fine-grained" in (info.note or "")
    assert "Workflows" in (info.note or "")


def test_validate_auth_failure(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kw: (401, {"message": "Bad credentials"}, {}),
    )
    info = client.validate_token()
    assert info.valid is False
    assert info.error == "Bad credentials"


def test_list_repos(monkeypatch) -> None:
    client = make_client()
    payload = [
        {
            "full_name": "octocat/hello",
            "private": False,
            "default_branch": "main",
            "clone_url": "https://github.com/octocat/hello.git",
            "html_url": "https://github.com/octocat/hello",
        }
    ]
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (200, payload, {}))
    repos = client.list_repos()
    assert repos[0]["full_name"] == "octocat/hello"
    assert repos[0]["clone_url"] == "https://github.com/octocat/hello.git"


def test_list_repos_error(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (403, None, {}))
    with pytest.raises(GitHubError):
        client.list_repos()


def test_get_repo(monkeypatch) -> None:
    client = make_client()
    payload = {
        "full_name": "octocat/hello",
        "default_branch": "main",
        "clone_url": "https://github.com/octocat/hello.git",
        "private": False,
    }
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (200, payload, {}))
    info = client.get_repo("octocat/hello")
    assert info["full_name"] == "octocat/hello"
    assert info["clone_url"] == "https://github.com/octocat/hello.git"


def test_get_repo_not_found(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kw: (404, {"message": "Not Found"}, {}),
    )
    with pytest.raises(GitHubNotFound):
        client.get_repo("octocat/nope")


def test_create_pr(monkeypatch) -> None:
    client = make_client()

    def fake_request(method, path, **kwargs):
        assert method == "POST"
        assert path == "/repos/octocat/hello/pulls"
        assert kwargs["json"]["head"] == "jalebi/7"
        assert kwargs["json"]["base"] == "main"
        return (201, {"number": 42}, {})

    monkeypatch.setattr(client, "_request", fake_request)
    number = client.create_pr(
        "octocat/hello", title="t", body="b", head="jalebi/7", base="main"
    )
    assert number == 42


def test_create_pr_error(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (422, None, {}))
    with pytest.raises(GitHubError):
        client.create_pr("octocat/hello", title="t", body="b", head="h", base="main")


def test_find_pr_by_head_found(monkeypatch) -> None:
    client = make_client()
    body = [
        {"number": 3, "state": "open", "head": {"ref": "jalebi/7"}},
        {"number": 4, "state": "open", "head": {"ref": "other"}},
    ]

    def fake_request(method, path, **kwargs):
        assert kwargs["params"]["head"] == "octocat:jalebi/7"
        assert kwargs["params"]["state"] == "open"
        return (200, body, {})

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.find_pr_by_head("octocat/hello", "jalebi/7") == 3


def test_find_pr_by_head_ignores_closed_and_merged(monkeypatch) -> None:
    """A stale closed/merged PR reusing the head ref must never be matched."""
    client = make_client()
    body = [
        {"number": 1, "state": "closed", "head": {"ref": "jalebi/7"}},
        {"number": 2, "state": "merged", "head": {"ref": "jalebi/7"}},
        {"number": 5, "state": "open", "head": {"ref": "jalebi/7"}},
    ]

    def fake_request(method, path, **kwargs):
        assert kwargs["params"]["state"] == "open"
        return (200, body, {})

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.find_pr_by_head("octocat/hello", "jalebi/7") == 5


def test_find_pr_by_head_none(monkeypatch) -> None:
    client = make_client()
    body = [
        {"number": 1, "state": "closed", "head": {"ref": "jalebi/7"}},
    ]

    def fake_request(method, path, **kwargs):
        assert kwargs["params"]["state"] == "open"
        return (200, body, {})

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.find_pr_by_head("octocat/hello", "jalebi/7") is None


def test_list_issues_excludes_prs(monkeypatch) -> None:
    client = make_client()
    body = [
        {"number": 1, "title": "bug", "html_url": "u", "state": "open"},
        {"number": 2, "title": "a PR", "pull_request": {"url": "x"}, "state": "open"},
    ]
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (200, body, {}))
    issues = client.list_issues("octocat/hello")
    assert [i["number"] for i in issues] == [1]


def test_get_issue(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kw: (
            200,
            {"number": 1, "title": "t", "body": "b", "html_url": "u", "state": "open"},
            {},
        ),
    )
    issue = client.get_issue("octocat/hello", 1)
    assert issue["number"] == 1
    assert issue["body"] == "b"


def test_get_issue_rejects_pr(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kw: (
            200,
            {"number": 4, "title": "t", "pull_request": {"url": "x"}},
            {},
        ),
    )
    with pytest.raises(GitHubError, match="pull request"):
        client.get_issue("octocat/hello", 4)


def test_comment_on_issue(monkeypatch) -> None:
    client = make_client()

    def fake_request(method, path, **kwargs):
        assert path == "/repos/octocat/hello/issues/1/comments"
        assert kwargs["json"]["body"] == "hi"
        return (201, {}, {})

    monkeypatch.setattr(client, "_request", fake_request)
    client.comment_on_issue("octocat/hello", 1, "hi")


def test_list_prs(monkeypatch) -> None:
    client = make_client()
    body = [
        {
            "number": 3,
            "title": "t",
            "html_url": "u",
            "state": "open",
            "base": {"ref": "main"},
            "head": {"ref": "jalebi/7"},
            "user": {"login": "octocat"},
        }
    ]
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (200, body, {}))
    prs = client.list_prs("octocat/hello")
    assert prs[0]["number"] == 3
    assert prs[0]["head"] == "jalebi/7"


def test_list_prs_marks_fork_heads(monkeypatch) -> None:
    client = make_client()
    body = [
        {
            "number": 7,
            "title": "fork PR",
            "html_url": "u",
            "state": "open",
            "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
            "head": {
                "ref": "feat/x",
                "sha": "abc",
                "repo": {"full_name": "fork/repo", "fork": True},
            },
            "user": {"login": "contrib"},
        },
        {
            "number": 8,
            "title": "same-repo PR",
            "html_url": "u",
            "state": "open",
            "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
            "head": {
                "ref": "feat/y",
                "sha": "def",
                "repo": {"full_name": "owner/repo", "fork": False},
            },
            "user": {"login": "owner"},
        },
    ]
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (200, body, {}))
    prs = client.list_prs("owner/repo")
    assert prs[0]["is_fork"] is True
    assert prs[0]["head_repo"] == "fork/repo"
    assert prs[0]["head_sha"] == "abc"
    assert prs[1]["is_fork"] is False
    assert prs[1]["head_repo"] == "owner/repo"


def test_get_pr_returns_fork_metadata(monkeypatch) -> None:
    client = make_client()
    payload = {
        "number": 7,
        "title": "t",
        "body": "b",
        "html_url": "u",
        "state": "open",
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
        "head": {
            "ref": "feat/x",
            "sha": "abc123",
            "repo": {
                "full_name": "fork/repo",
                "fork": True,
                "clone_url": "https://github.com/fork/repo.git",
            },
        },
        "maintainer_can_modify": True,
        "user": {"login": "contrib"},
    }
    monkeypatch.setattr(
        client, "_request", lambda method, path, **kw: (200, payload, {})
    )
    pr = client.get_pr("owner/repo", 7)
    assert pr["head"] == "feat/x"
    assert pr["head_repo"] == "fork/repo"
    assert pr["head_clone_url"] == "https://github.com/fork/repo.git"
    assert pr["is_fork"] is True
    assert pr["maintainer_can_modify"] is True
    assert pr["head_sha"] == "abc123"


def test_post_pr_review(monkeypatch) -> None:
    """GitHub returns 200 OK on a successful review POST, not 201."""
    client = make_client()

    def fake_request(method, path, **kwargs):
        assert path == "/repos/octocat/hello/pulls/3/reviews"
        assert kwargs["json"]["event"] == "COMMENT"
        return (200, {}, {})

    monkeypatch.setattr(client, "_request", fake_request)
    client.post_pr_review("octocat/hello", 3, "looks good")


def test_post_pr_review_accepts_legacy_201(monkeypatch) -> None:
    """Some mocks / older API responses return 201 — both must be accepted."""
    client = make_client()
    monkeypatch.setattr(client, "_request", lambda *a, **kw: (201, {}, {}))
    client.post_pr_review("octocat/hello", 3, "looks good")


def test_post_pr_review_raises_on_real_failure(monkeypatch) -> None:
    """Non-2xx responses must still raise so the error step is appended."""
    client = make_client()
    monkeypatch.setattr(client, "_request", lambda *a, **kw: (422, {}, {}))
    with pytest.raises(GitHubError, match="HTTP 422"):
        client.post_pr_review("octocat/hello", 3, "looks good")


def test_list_branches(monkeypatch) -> None:
    client = make_client()
    body = [{"name": "main"}, {"name": "dev"}]
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (200, body, {}))
    assert client.list_branches("octocat/hello") == ["main", "dev"]


def test_list_repos_follows_pagination(monkeypatch) -> None:
    """list_repos must follow Link rel=next until the list is exhausted."""
    from jalebi.github import GitHubClient

    client = GitHubClient("ghp_test")
    pages = [
        ([{"full_name": f"o/repo{i}"} for i in range(2)], {
            "link": '<https://api.github.com/user/repos?per_page=100&page=2>; rel="next", '
                    '<https://api.github.com/user/repos?per_page=100&page=2>; rel="last"'
        }),
        ([{"full_name": "o/repo2"}], {}),
    ]
    calls: list[int] = []
    seen = iter(pages)

    def fake_request(method, path, **kwargs):
        params = kwargs.get("params") or {}
        calls.append(params.get("page", 1))
        body, headers = next(seen)
        return 200, body, headers

    monkeypatch.setattr(client, "_request", fake_request)
    names = [r["full_name"] for r in client.list_repos()]
    assert names == ["o/repo0", "o/repo1", "o/repo2"]
    assert calls == [1, 2]


# ---- Phase 4 T2.1 — poller-facing read methods with ETag --------------------


def _make_response(status_code: int, body=None, headers=None):
    """Build a minimal ``httpx.Response`` for the poller's ``_request_etag``."""
    import httpx

    return httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("GET", "https://api.github.com/x"),
        headers=headers or {},
    )


def test_list_open_prs_returns_mergeable_and_head_sha(monkeypatch) -> None:


    client = make_client()
    prs = [
        {
            "number": 9,
            "head": {"ref": "jalebi/7", "sha": "abc123"},
            "mergeable": True,
        }
    ]
    monkeypatch.setattr(
        client._http,
        "request",
        lambda method, path, **kw: _make_response(200, prs, {"ETag": 'W/"e1"'}),
    )
    payload, etag, not_modified = client.list_open_prs("owner/repo")
    assert etag == 'W/"e1"'
    assert not_modified is False
    assert payload == [
        {"number": 9, "head": "jalebi/7", "head_sha": "abc123", "mergeable": True}
    ]


def test_list_open_prs_sends_if_none_match_and_304(monkeypatch) -> None:

    client = make_client()

    def fake_request(method, path, **kw):
        headers = kw.get("headers") or {}
        if "If-None-Match" in headers:
            return _make_response(304, None, {"ETag": headers["If-None-Match"]})
        return _make_response(200, [{"number": 1, "head": {"ref": "x", "sha": "s"}}], {})

    monkeypatch.setattr(client._http, "request", fake_request)
    # First call — no etag yet.
    body1, etag1, nm1 = client.list_open_prs("owner/repo")
    assert nm1 is False
    assert etag1 is None
    # Second call with the same etag → 304.
    body2, etag2, nm2 = client.list_open_prs("owner/repo", etag='W/"e1"')
    assert nm2 is True
    assert body2 == []


def test_list_check_runs_for_ref_hits_commit_status_endpoint(monkeypatch) -> None:
    from jalebi.github import PR_STATUS_PATH

    client = make_client()
    captured_paths = []

    def fake_request(method, path, **kw):
        captured_paths.append(path)
        return _make_response(
            200,
            {
                "state": "success",
                "total_count": 1,
                "statuses": [{"state": "success", "context": "ci/test"}],
            },
            {"ETag": 'W/"ci2"'},
        )

    monkeypatch.setattr(client._http, "request", fake_request)
    body, etag, not_modified = client.list_check_runs_for_ref("owner/repo", "abc123")
    assert not_modified is False
    assert etag == 'W/"ci2"'
    assert body["state"] == "success"
    assert body["total_count"] == 1
    assert captured_paths[0] == PR_STATUS_PATH.format(
        full_name="owner/repo", ref="abc123"
    )


def test_list_reviews_for_pr_returns_all_review_states(monkeypatch) -> None:

    client = make_client()
    reviews = [
        {"state": "APPROVED", "submitted_at": "2026-08-17T00:00:00Z"},
        {"state": "CHANGES_REQUESTED", "submitted_at": "2026-08-18T00:00:00Z"},
    ]
    monkeypatch.setattr(
        client._http,
        "request",
        lambda method, path, **kw: _make_response(200, reviews, {"ETag": 'W/"rv1"'}),
    )
    body, etag, _ = client.list_reviews_for_pr("owner/repo", 3)
    assert etag == 'W/"rv1"'
    assert [r["state"] for r in body] == ["APPROVED", "CHANGES_REQUESTED"]


def test_list_open_prs_raises_on_401(monkeypatch) -> None:
    from jalebi.github import GitHubUnauthorized

    client = make_client()
    monkeypatch.setattr(
        client._http, "request", lambda *a, **kw: _make_response(401, None)
    )
    with pytest.raises(GitHubUnauthorized):
        client.list_open_prs("owner/repo")


# -- rate-limit backoff --------------------------------------------------------


def _seq_request(seq):
    """Fake ``_http.request`` returning queued responses, then repeating the last."""
    def fake(method, path, **kwargs):
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return fake


def test_retry_wait_classifies_only_rate_limits() -> None:
    assert GitHubClient._retry_wait(200, {}) is None
    assert GitHubClient._retry_wait(403, {}) is None  # ordinary 403 → no retry
    assert GitHubClient._retry_wait(403, {"X-RateLimit-Remaining": "0"}) == 1.0
    assert GitHubClient._retry_wait(403, {"Retry-After": "2"}) == 2.0  # secondary limit
    assert GitHubClient._retry_wait(429, {}) == 1.0
    assert GitHubClient._retry_wait(429, {"Retry-After": "3"}) == 3.0
    assert GitHubClient._retry_wait(429, {"Retry-After": "-5"}) == 0.0  # never negative


def test_retry_wait_parses_http_date_and_handles_malformed_headers(monkeypatch) -> None:
    monkeypatch.setattr("jalebi.github.time.time", lambda: 100.0)
    retry_at = formatdate(110.0, usegmt=True)
    assert GitHubClient._retry_wait(429, {"Retry-After": retry_at}) == 10.0
    assert GitHubClient._retry_wait(429, {"Retry-After": "not-a-date"}) == 1.0
    assert GitHubClient._retry_wait(
        403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "not-a-time"}
    ) == 1.0


def test_send_retries_429_then_succeeds(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("jalebi.github.time.sleep", lambda s: sleeps.append(s))
    client = make_client()
    calls = 0

    def fake(method, path, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _make_response(429, None, {"Retry-After": "2"})
        return _make_response(200, {"ok": True})

    monkeypatch.setattr(client._http, "request", fake)
    status, body, _ = client._request("GET", "/x")
    assert status == 200 and body == {"ok": True}
    assert calls == 2
    assert sleeps == [2.0]


def test_send_retries_primary_403_and_caps_wait(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("jalebi.github.time.sleep", lambda s: sleeps.append(s))
    client = make_client()
    reset = str(int(time.time()) + 3600)
    monkeypatch.setattr(
        client._http,
        "request",
        _seq_request(
            [
                _make_response(
                    403, None, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}
                ),
                _make_response(200, {"ok": True}),
            ]
        ),
    )
    status, _, _ = client._request("GET", "/x")
    assert status == 200
    assert sleeps == [RATE_LIMIT_MAX_WAIT]


def test_send_does_not_retry_plain_403(monkeypatch) -> None:
    sleeps: list[float] = []
    calls = 0

    def fake(method, path, **kwargs):
        nonlocal calls
        calls += 1
        return _make_response(403, None)

    monkeypatch.setattr("jalebi.github.time.sleep", lambda s: sleeps.append(s))
    client = make_client()
    monkeypatch.setattr(client._http, "request", fake)
    status, _, _ = client._request("GET", "/x")
    assert status == 403 and calls == 1 and sleeps == []


def test_send_does_not_retry_rate_limited_write(monkeypatch) -> None:
    calls = 0

    def fake(method, path, **kwargs):
        nonlocal calls
        calls += 1
        return _make_response(429, None, {"Retry-After": "1"})

    client = make_client()
    monkeypatch.setattr(client._http, "request", fake)
    status, _, _ = client._request("POST", "/x", json={"value": True})
    assert status == 429
    assert calls == 1


def test_send_gives_up_after_max_retries(monkeypatch) -> None:
    sleeps: list[float] = []
    calls = 0

    def fake(method, path, **kwargs):
        nonlocal calls
        calls += 1
        return _make_response(429, None, {"Retry-After": "1"})

    monkeypatch.setattr("jalebi.github.time.sleep", lambda s: sleeps.append(s))
    client = make_client()
    monkeypatch.setattr(client._http, "request", fake)
    status, _, _ = client._request("GET", "/x")
    assert status == 429
    assert calls == RATE_LIMIT_MAX_RETRIES + 1
    assert len(sleeps) == RATE_LIMIT_MAX_RETRIES


def test_request_etag_retries_rate_limit(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("jalebi.github.time.sleep", lambda s: sleeps.append(s))
    client = make_client()
    monkeypatch.setattr(
        client._http,
        "request",
        _seq_request(
            [
                _make_response(429, None, {"Retry-After": "1"}),
                _make_response(200, {"n": 1}, {"ETag": 'W/"a"'}),
            ]
        ),
    )
    body, etag, not_modified = client._request_etag("/repos/o/r/pulls")
    assert body == {"n": 1} and etag == 'W/"a"' and not_modified is False
    assert sleeps == [1.0]
