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
