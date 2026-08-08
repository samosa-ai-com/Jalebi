"""Tests for the ntfy notification sender (notify.py)."""

import httpx

from jalebi import notify, settings


def test_endpoint_bare_topic_defaults_to_ntfy_sh() -> None:
    assert notify.endpoint("my-jalebi") == "https://ntfy.sh/my-jalebi"


def test_endpoint_full_url_passthrough() -> None:
    assert notify.endpoint("https://ntfy.example.com/room") == "https://ntfy.example.com/room"


def test_endpoint_empty_is_none() -> None:
    assert notify.endpoint("") is None
    assert notify.endpoint("   ") is None
    assert notify.endpoint(None) is None


def test_send_unconfigured_returns_error(session) -> None:
    settings.set_setting(session, "ntfy_topic", "")
    ok, error = notify.send(session, "t", "m")
    assert ok is False
    assert "not configured" in (error or "")


def test_send_posts_to_endpoint(session, monkeypatch) -> None:
    settings.set_setting(session, "ntfy_topic", "jalebi-room")
    captured: dict = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)
    ok, error = notify.send(session, "Task done", "all good", tags=notify.TAGS_OK)
    assert ok is True and error is None
    assert captured["url"] == "https://ntfy.sh/jalebi-room"
    assert captured["json"]["title"] == "Task done"
    assert captured["json"]["message"] == "all good"
    assert captured["json"]["tags"] == ["white_check_mark"]


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
