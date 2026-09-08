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


def test_enabled_backends_validated_and_listed(client: FlaskClient) -> None:
    """enabled_backends: non-empty known subset; default must stay enabled."""
    body = client.get("/api/backends").get_json()
    assert body["backends"] == [
        "opencode",
        "codex",
        "claude",
        "pi",
        "kilo",
        "qwen",
        "cline",
        "goose",
        "grok",
        "commandcode",
        "agy",
    ]
    assert body["enabled"] == ["opencode", "codex", "claude"]
    assert body["default"] == "opencode"

    # A valid subset saves and is reflected.
    resp = client.post(
        "/api/settings", json={"key": "enabled_backends", "value": ["opencode", "codex"]}
    )
    assert resp.status_code == 200
    assert client.get("/api/backends").get_json()["enabled"] == ["opencode", "codex"]

    # The default backend can't be switched off (change the default first).
    resp = client.post("/api/settings", json={"key": "enabled_backends", "value": ["codex"]})
    assert resp.status_code == 400
    # Empty / unknown / non-list rejected.
    for bad in ([], ["gemini"], "opencode", [None]):
        resp = client.post("/api/settings", json={"key": "enabled_backends", "value": bad})
        assert resp.status_code == 400, bad

    # The default backend must be an enabled one.
    resp = client.post("/api/settings", json={"key": "default_backend", "value": "claude"})
    assert resp.status_code == 400
    resp = client.post("/api/settings", json={"key": "default_backend", "value": "codex"})
    assert resp.status_code == 200


def test_timezones_endpoint(client: FlaskClient) -> None:
    body = client.get("/api/timezones").get_json()
    assert body["local"] == "local"
    assert "Asia/Kolkata" in body["common"]
    assert "UTC" in body["all"]
    assert body["all"] == sorted(body["all"])


def test_ntfy_url_no_longer_valid(client: FlaskClient) -> None:
    """ntfy_url was merged into ntfy_topic — the old key is rejected."""
    resp = client.post("/api/settings", json={"key": "ntfy_url", "value": "https://ntfy.sh"})
    assert resp.status_code == 400


def test_retry_policy_accepts_attempt_cap_and_patterns(client: FlaskClient) -> None:
    """retry_policy accepts max_attempts + non_retryable_patterns; rejects bad shapes."""
    resp = client.post(
        "/api/settings",
        json={
            "key": "retry_policy",
            "value": {
                "auto_retry": True,
                "max_attempts": 5,
                "non_retryable_patterns": ["model not found"],
            },
        },
    )
    assert resp.status_code == 200
    body = client.get("/api/settings").get_json()
    assert body["retry_policy"]["max_attempts"] == 5
    assert body["retry_policy"]["non_retryable_patterns"] == ["model not found"]
    # Legacy shape still valid.
    resp = client.post("/api/settings", json={"key": "retry_policy", "value": {"auto_retry": True}})
    assert resp.status_code == 200
    # max_attempts must be an int >= 1; patterns must be a str list.
    resp = client.post(
        "/api/settings",
        json={"key": "retry_policy", "value": {"auto_retry": True, "max_attempts": 0}},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/settings",
        json={"key": "retry_policy", "value": {"auto_retry": True, "max_attempts": "many"}},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/settings",
        json={"key": "retry_policy", "value": {"auto_retry": True, "non_retryable_patterns": "x"}},
    )
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

    resp = client.post("/api/settings", json={"key": "default_backend", "value": "codex"})
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


def test_default_backend_and_model_settings(client: FlaskClient) -> None:
    """default_backend accepts registered adapters; default_model is required."""
    for cli in ("opencode", "codex", "claude"):
        resp = client.post("/api/settings", json={"key": "default_backend", "value": cli})
        assert resp.status_code == 200, f"{cli} should be accepted"
    resp = client.post("/api/settings", json={"key": "default_backend", "value": "gemini"})
    assert resp.status_code == 400

    resp = client.post("/api/settings", json={"key": "default_model", "value": "m-default"})
    assert resp.status_code == 200
    body = client.get("/api/settings").get_json()
    assert body["default_model"] == "m-default"
    # A default model must always be configured (reject empty / blank).
    resp = client.post("/api/settings", json={"key": "default_model", "value": ""})
    assert resp.status_code == 400
    resp = client.post("/api/settings", json={"key": "default_model", "value": "   "})
    assert resp.status_code == 400


def test_models_endpoint_cli_query_param(client: FlaskClient, monkeypatch) -> None:
    """GET /api/models?cli=<backend> returns that backend's models regardless of
    the global default_backend setting (used by the forms)."""
    from jalebi.adapters import get_adapter
    from jalebi.adapters.opencode import OpenCodeAdapter

    # The real opencode adapter shells out to the `opencode models` CLI
    # (~1.5s process spawn); this test covers param routing, not the binary.
    monkeypatch.setattr(OpenCodeAdapter, "list_models", lambda self: ["m1"])

    client.post("/api/settings", json={"key": "default_backend", "value": "opencode"})

    body = client.get("/api/models?cli=claude").get_json()
    assert body["cli"] == "claude"
    assert body["models"] == get_adapter("claude").list_models()

    body = client.get("/api/models?cli=codex").get_json()
    assert body["cli"] == "codex"
    assert body["models"] == get_adapter("codex").list_models()

    # An unknown backend is a clean empty list, never a 500.
    resp = client.get("/api/models?cli=gemini")
    assert resp.status_code == 200
    assert resp.get_json()["models"] == []

    # The per-cli override still wins for the requested backend.
    client.post(
        "/api/settings",
        json={"key": "adapter_model_lists", "value": {"claude": ["claude-override"]}},
    )
    body = client.get("/api/models?cli=claude").get_json()
    assert body["models"] == ["claude-override"]

    # No param → the default_backend setting's backend.
    body = client.get("/api/models").get_json()
    assert body["cli"] == "opencode"
    assert body["models"] == ["m1"]
