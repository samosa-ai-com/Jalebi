import pytest

from jalebi.github import GitHubClient, GitHubError, GitHubNotFound


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
    assert "fine-grained" in (info.note or "")


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
        {"number": 3, "head": {"ref": "jalebi/7"}},
        {"number": 4, "head": {"ref": "other"}},
    ]

    def fake_request(method, path, **kwargs):
        assert kwargs["params"]["head"] == "octocat:jalebi/7"
        return (200, body, {})

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.find_pr_by_head("octocat/hello", "jalebi/7") == 3


def test_find_pr_by_head_none(monkeypatch) -> None:
    client = make_client()
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (200, [], {}))
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


def test_post_pr_review(monkeypatch) -> None:
    client = make_client()

    def fake_request(method, path, **kwargs):
        assert path == "/repos/octocat/hello/pulls/3/reviews"
        assert kwargs["json"]["event"] == "COMMENT"
        return (201, {}, {})

    monkeypatch.setattr(client, "_request", fake_request)
    client.post_pr_review("octocat/hello", 3, "looks good")


def test_list_branches(monkeypatch) -> None:
    client = make_client()
    body = [{"name": "main"}, {"name": "dev"}]
    monkeypatch.setattr(client, "_request", lambda method, path, **kw: (200, body, {}))
    assert client.list_branches("octocat/hello") == ["main", "dev"]
