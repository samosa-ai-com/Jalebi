from flask.testing import FlaskClient

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


def test_ntfy_url_rejects_non_http(client: FlaskClient) -> None:
    resp = client.post("/api/settings", json={"key": "ntfy_url", "value": "htp:/ntfy.sh"})
    assert resp.status_code == 400
    resp = client.post("/api/settings", json={"key": "ntfy_url", "value": "https://ntfy.sh"})
    assert resp.status_code == 200
    resp = client.post("/api/settings", json={"key": "ntfy_url", "value": ""})
    assert resp.status_code == 200
