import re

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


def test_spa_assets_served(client: FlaskClient) -> None:
    index = client.get("/").get_data(as_text=True)
    match = re.search(r"assets/[^\"']+", index)
    assert match, "index.html should reference a built asset"
    asset_resp = client.get("/" + match.group(0))
    assert asset_resp.status_code == 200


def test_api_still_served_alongside_spa(client: FlaskClient) -> None:
    assert client.get("/api/health").status_code == 200


def test_spa_missing_build_returns_503(client: FlaskClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "WEB_DIST", tmp_path / "does-not-exist")
    resp = client.get("/")
    assert resp.status_code == 503
    assert client.get("/tasks/1").status_code == 503
