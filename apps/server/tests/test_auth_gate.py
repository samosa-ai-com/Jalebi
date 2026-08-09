"""Tests for the Basic-auth gate + failed-login ntfy notification (PRD §F13)."""

import time

from jalebi import secrets, settings


def _wait_for(sent: list, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not sent and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sent, "ntfy push thread never delivered"


def _pw_app(app, config, password="hunter2"):
    app.config["JALEBI_CONFIG"] = config.__class__(
        host="127.0.0.1", port=3456, data_dir=config.data_dir, password=password
    )
    return app


def test_gate_opens_with_correct_password(client, app, config) -> None:
    _pw_app(app, config)
    assert client.get("/api/settings", auth=("jalebi", "hunter2")).status_code == 200


def test_gate_rejects_wrong_password(client, app, config) -> None:
    _pw_app(app, config)
    assert client.get("/api/settings", auth=("jalebi", "wrong")).status_code == 401
    assert client.get("/api/settings").status_code == 401


def test_gate_skips_when_no_password(client, app, config) -> None:
    assert client.get("/api/settings").status_code == 200


def test_health_exempt_from_gate(client, app, config) -> None:
    _pw_app(app, config)
    assert client.get("/api/health").status_code == 200


def test_failed_login_pushes_ntfy(client, app, config, session, monkeypatch) -> None:
    """A wrong password attempt sends a masked ntfy push (throttled per client)."""
    _pw_app(app, config)
    settings.set_setting(session, "ntfy_topic", "my-jalebi")

    sent: list[tuple[str, str]] = []

    def fake_send(session, title, message, **kwargs):
        sent.append((title, message))
        return True, None

    from jalebi.app import _failed_login_pushes

    monkeypatch.setattr("jalebi.app.notify.send", fake_send)
    _failed_login_pushes.clear()
    client.get("/api/settings", auth=("attacker", "nope"))
    _wait_for(sent)
    assert len(sent) == 1
    title, message = sent[0]
    assert "failed login" in title.lower()

    # Throttle: a second attempt from the same client within the window is a no-op.
    client.get("/api/settings", auth=("attacker", "nope"))
    time.sleep(0.1)
    assert len(sent) == 1


def test_failed_login_never_ships_real_token_username(
    client, app, config, session, monkeypatch
) -> None:
    """A real PAT used as the attempted username is masked before shipping."""
    _pw_app(app, config)
    settings.set_setting(session, "ntfy_topic", "my-jalebi")
    secrets.add_github_token(config, "test", "ghp_AKIA0123456789ABCDEFSECRET")

    sent: list[tuple[str, str]] = []

    def fake_send(session, title, message, **kwargs):
        sent.append((title, message))
        return True, None

    from jalebi.app import _failed_login_pushes

    monkeypatch.setattr("jalebi.app.notify.send", fake_send)
    _failed_login_pushes.clear()
    client.get("/api/settings", auth=("ghp_AKIA0123456789ABCDEFSECRET", "nope"))
    _wait_for(sent)
    assert len(sent) == 1
    title, message = sent[0]
    assert "ghp_AKIA0123456789ABCDEFSECRET" not in message
