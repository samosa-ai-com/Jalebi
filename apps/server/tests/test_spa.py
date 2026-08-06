from flask.testing import FlaskClient

from jalebi import app as app_module


def test_spa_serves_index(client: FlaskClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'id="root"' in resp.data


def test_spa_falls_back_to_index_for_client_routes(client: FlaskClient) -> None:
    resp = client.get("/tasks/7")
    assert resp.status_code == 200
    assert b'id="root"' in resp.data


def test_spa_assets_served(client: FlaskClient, tmp_path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div><script src="/assets/app.js"></script>')
    (dist / "assets" / "app.js").write_text("console.log('x');")
    monkeypatch.setattr(app_module, "WEB_DIST", dist)

    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/app.js").data == b"console.log('x');"


def test_api_still_served_alongside_spa(client: FlaskClient) -> None:
    assert client.get("/api/health").status_code == 200


def test_unknown_api_route_returns_404_json(client: FlaskClient) -> None:
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()


def test_spa_missing_build_returns_503(client: FlaskClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "WEB_DIST", tmp_path / "does-not-exist")
    resp = client.get("/")
    assert resp.status_code == 503
    assert client.get("/tasks/1").status_code == 503
