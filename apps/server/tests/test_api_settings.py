from flask.testing import FlaskClient

from jalebi import clock
from jalebi.settings import DEFAULTS


def test_settings_endpoint_returns_defaults(client: FlaskClient) -> None:
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["concurrency"] == DEFAULTS["concurrency"]
    assert body["auto_publish"] is DEFAULTS["auto_publish"]
    assert body["default_timeout_minutes"] == DEFAULTS["default_timeout_minutes"]
    assert body["ntfy_topic"] == ""


def test_settings_endpoint_reflects_stored_value(client: FlaskClient) -> None:
    client.post("/api/settings", json={"key": "concurrency", "value": 6})
    body = client.get("/api/settings").get_json()
    assert body["concurrency"] == 6


def test_secret_patterns_reject_invalid_regex(client: FlaskClient) -> None:
    resp = client.post("/api/settings", json={"key": "secret_patterns", "value": ["[invalid"]})
    assert resp.status_code == 400
    resp = client.post("/api/settings", json={"key": "secret_patterns", "value": ["AKIA[0-9]{16}"]})
    assert resp.status_code == 200


def test_ntfy_topic_accepts_topic_or_url(client: FlaskClient) -> None:
    """The merged ntfy endpoint accepts a bare topic or an http(s) URL."""
    # Bare topic is valid.
    resp = client.post("/api/settings", json={"key": "ntfy_topic", "value": "my-jalebi"})
    assert resp.status_code == 200
    # Full URL is valid.
    resp = client.post("/api/settings", json={"key": "ntfy_topic", "value": "https://ntfy.sh/room"})
    assert resp.status_code == 200
    # Empty clears it.
    resp = client.post("/api/settings", json={"key": "ntfy_topic", "value": ""})
    assert resp.status_code == 200
    # A topic must not smuggle a path or spaces.
    resp = client.post("/api/settings", json={"key": "ntfy_topic", "value": "a b"})
    assert resp.status_code == 400
    resp = client.post("/api/settings", json={"key": "ntfy_topic", "value": "jalebi/room"})
    assert resp.status_code == 400


def test_ntfy_url_no_longer_valid(client: FlaskClient) -> None:
    """ntfy_url was merged into ntfy_topic — the old key is rejected."""
    resp = client.post("/api/settings", json={"key": "ntfy_url", "value": "https://ntfy.sh"})
    assert resp.status_code == 400


def test_timezone_setting_validated(client: FlaskClient) -> None:
    """The timezone setting accepts ``local``/IANA names and rejects unknowns."""
    assert client.get("/api/settings").get_json()["timezone"] == "local"
    resp = client.post("/api/settings", json={"key": "timezone", "value": "Asia/Kolkata"})
    assert resp.status_code == 200
    assert client.get("/api/settings").get_json()["timezone"] == "Asia/Kolkata"
    # Saving a timezone applies it live (the wall clock follows it).
    assert clock.zone_name() == "Asia/Kolkata"
    resp = client.post("/api/settings", json={"key": "timezone", "value": "Mars/Olympus"})
    assert resp.status_code == 400
    resp = client.post("/api/settings", json={"key": "timezone", "value": "local"})
    assert resp.status_code == 200
    assert clock.zone_name() == "local"


def test_notify_settings_validators(client: FlaskClient) -> None:
    toggles = (
        "notify_on_done",
        "notify_on_failed",
        "notify_on_progress",
        "notify_on_needs_approval",
    )
    for key in toggles:
        resp = client.post("/api/settings", json={"key": key, "value": True})
        assert resp.status_code == 200
        resp = client.post("/api/settings", json={"key": key, "value": "yes"})
        assert resp.status_code == 400
    interval = "notify_progress_interval_minutes"
    resp = client.post("/api/settings", json={"key": interval, "value": 15})
    assert resp.status_code == 200
    resp = client.post("/api/settings", json={"key": interval, "value": 0})
    assert resp.status_code == 400


def test_adapter_model_lists_validated_and_stored(client: FlaskClient) -> None:
    """adapter_model_lists accepts {cli: [model names]} and rejects unknown/ill-shaped."""
    resp = client.post(
        "/api/settings", json={"key": "adapter_model_lists", "value": {"codex": ["m1", "m2"]}}
    )
    assert resp.status_code == 200
    body = client.get("/api/settings").get_json()
    assert body["adapter_model_lists"] == {"codex": ["m1", "m2"]}
    # Empty dict (the default) is valid.
    resp = client.post("/api/settings", json={"key": "adapter_model_lists", "value": {}})
    assert resp.status_code == 200


def test_models_endpoint_uses_adapter_model_lists_override(client: FlaskClient) -> None:
    """GET /api/models returns the owner override for the active cli before the adapter."""
    from jalebi.adapters import get_adapter

    resp = client.post("/api/settings", json={"key": "agent_cli", "value": "codex"})
    assert resp.status_code == 200
    resp = client.post(
        "/api/settings",
        json={"key": "adapter_model_lists", "value": {"codex": ["gpt-override", "gpt-2"]}},
    )
    assert resp.status_code == 200
    body = client.get("/api/models").get_json()
    assert body["cli"] == "codex"
    assert body["models"] == ["gpt-override", "gpt-2"]
    # With no override, the adapter's own list is returned.
    resp = client.post(
        "/api/settings", json={"key": "adapter_model_lists", "value": {}}
    )
    assert resp.status_code == 200
    body = client.get("/api/models").get_json()
    assert body["cli"] == "codex"
    assert body["models"] == get_adapter("codex").list_models()
    # An explicit empty list is authoritative (clears the dropdown), not ignored.
    resp = client.post(
        "/api/settings", json={"key": "adapter_model_lists", "value": {"codex": []}}
    )
    assert resp.status_code == 200
    body = client.get("/api/models").get_json()
    assert body["cli"] == "codex"
    assert body["models"] == []


def test_agent_cli_accepts_every_registered_adapter(client: FlaskClient) -> None:
    """The agent_cli setting accepts every registered adapter and rejects unknowns."""
    for cli in ("opencode", "codex", "claude"):
        resp = client.post("/api/settings", json={"key": "agent_cli", "value": cli})
        assert resp.status_code == 200, f"{cli} should be accepted"
    resp = client.post("/api/settings", json={"key": "agent_cli", "value": "gemini"})
    assert resp.status_code == 400
