def test_health_ok(client) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_password_gate_enforces_basic_auth(config, tmp_path) -> None:
    import base64

    from flask import Flask

    from jalebi import db
    from jalebi.app import create_app
    from jalebi.config import Config

    gated = Config(
        host="127.0.0.1", port=3456, data_dir=tmp_path / "gated", password="hunter2"
    )
    app: Flask = create_app(gated)
    client = app.test_client()

    # /api/health is exempt.
    assert client.get("/api/health").status_code == 200
    # Everything else 401s without credentials…
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/").status_code == 401
    # …and the SPA route advertises Basic auth.
    assert "Basic" in client.get("/").headers.get("WWW-Authenticate", "")

    creds = base64.b64encode(b"jalebi:hunter2").decode()
    assert client.get("/api/tasks", headers={"Authorization": f"Basic {creds}"}).status_code == 200

    db.close_db()
