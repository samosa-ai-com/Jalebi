"""Tests for POST /api/notify/test."""

from flask.testing import FlaskClient


def test_notify_test_unconfigured_returns_400(client: FlaskClient) -> None:
    resp = client.post("/api/notify/test")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_notify_test_sends_and_returns_ok(app, client: FlaskClient, session, monkeypatch) -> None:
    from jalebi import settings

    settings.set_setting(session, "ntfy_topic", "room")

    class FakeResp:
        status_code = 200

    monkeypatch.setattr("jalebi.notify.httpx.post", lambda *a, **k: FakeResp())
    resp = client.post("/api/notify/test")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_notify_test_disabled_still_sends_with_warning(
    app, client: FlaskClient, session, monkeypatch
) -> None:
    """The test endpoint bypasses the master switch but surfaces a warning."""
    from jalebi import settings

    settings.set_setting(session, "ntfy_topic", "room")
    settings.set_setting(session, "ntfy_enabled", False)

    class FakeResp:
        status_code = 200

    monkeypatch.setattr("jalebi.notify.httpx.post", lambda *a, **k: FakeResp())
    resp = client.post("/api/notify/test")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "disabled" in (body.get("warning") or "")


def test_notify_test_rejected_shows_error(app, client: FlaskClient, session, monkeypatch) -> None:
    from jalebi import settings

    settings.set_setting(session, "ntfy_topic", "room")

    class FakeResp:
        status_code = 403

    monkeypatch.setattr("jalebi.notify.httpx.post", lambda *a, **k: FakeResp())
    resp = client.post("/api/notify/test")
    assert resp.status_code == 400
    assert "403" in resp.get_json()["error"]
