"""Tests for the ntfy notification sender (notify.py)."""

import httpx

from jalebi import notify, settings


def test_resolve_bare_topic_defaults_to_ntfy_sh() -> None:
    assert notify.resolve("my-jalebi") == ("https://ntfy.sh", "my-jalebi")


def test_resolve_full_url_splits_base_and_topic() -> None:
    assert notify.resolve("https://ntfy.example.com/room") == (
        "https://ntfy.example.com",
        "room",
    )
    assert notify.resolve("https://ntfy.example.com") is None  # no topic


def test_resolve_empty_is_none() -> None:
    assert notify.resolve("") is None
    assert notify.resolve("   ") is None
    assert notify.resolve(None) is None


def test_send_unconfigured_returns_error(session) -> None:
    settings.set_setting(session, "ntfy_topic", "")
    ok, error = notify.send(session, "t", "m")
    assert ok is False
    assert "not configured" in (error or "")


def test_send_posts_json_to_server_root(session, monkeypatch) -> None:
    """JSON publishing: POST to the server ROOT with topic in the body (not to
    /topic — that would render the raw JSON as the message)."""
    settings.set_setting(session, "ntfy_topic", "jalebi-room")
    captured: dict = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)
    ok, error = notify.send(
        session,
        "Task done",
        "all good",
        tags=notify.TAGS_OK,
        click="http://127.0.0.1:3456/tasks/3",
        actions=[{"action": "view", "label": "Open task", "url": "http://127.0.0.1:3456/tasks/3"}],
    )
    assert ok is True and error is None
    assert captured["url"] == "https://ntfy.sh"  # server root, not /jalebi-room
    body = captured["json"]
    assert body["topic"] == "jalebi-room"
    assert body["title"] == "Task done"
    assert body["message"] == "all good"
    assert body["markdown"] is True
    assert body["tags"] == ["white_check_mark"]
    assert body["click"] == "http://127.0.0.1:3456/tasks/3"
    assert body["actions"][0]["label"] == "Open task"


def test_send_posts_to_self_hosted_root(session, monkeypatch) -> None:
    settings.set_setting(session, "ntfy_topic", "https://ntfy.example.com/room")
    captured: dict = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)
    ok, _ = notify.send(session, "t", "m")
    assert ok is True
    assert captured["url"] == "https://ntfy.example.com"
    assert captured["json"]["topic"] == "room"


def test_send_masks_before_posting(session, monkeypatch) -> None:
    settings.set_setting(session, "ntfy_topic", "room")
    captured: dict = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)

    def masker(text):
        return text.replace("SECRET", "***")

    ok, _ = notify.send(session, "title", "token SECRET leaked", masker=masker)
    assert ok is True
    assert "SECRET" not in captured["json"]["message"]


def test_send_http_failure_returns_error(session, monkeypatch) -> None:
    settings.set_setting(session, "ntfy_topic", "room")

    def boom(url, json=None, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("jalebi.notify.httpx.post", boom)
    ok, error = notify.send(session, "t", "m")
    assert ok is False
    assert "down" in (error or "")


def test_send_rejected_status_returns_error(session, monkeypatch) -> None:
    settings.set_setting(session, "ntfy_topic", "room")

    class FakeResp:
        status_code = 400

    monkeypatch.setattr("jalebi.notify.httpx.post", lambda *a, **k: FakeResp())
    ok, error = notify.send(session, "t", "m")
    assert ok is False
    assert "400" in (error or "")


def test_masked_preview_never_full_value() -> None:
    assert "SECRETVALUE" not in notify.masked("SECRETVALUE")
    assert notify.masked("SECRETVALUE").startswith("SECR")
    assert notify.masked("").strip() == ""
