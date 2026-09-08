def test_health_ok(client) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_main_refuses_to_start_without_password(monkeypatch, tmp_path) -> None:
    """0.0.0.0 default is intentional (LAN use) — startup fails closed without a password."""
    import pytest

    import jalebi.app as appmod
    from jalebi.config import Config

    monkeypatch.setattr(
        appmod, "load_config", lambda: Config(data_dir=tmp_path / "data", password="")
    )

    def _no_create(_config):
        raise AssertionError("create_app must not run without a password")

    monkeypatch.setattr(appmod, "create_app", _no_create)
    with pytest.raises(SystemExit) as exc:
        appmod.main()
    assert exc.value.code == 2


def test_main_starts_with_password(monkeypatch, tmp_path) -> None:
    """Sanity: a set password passes the startup gate (server run itself stubbed)."""
    import jalebi.app as appmod
    from jalebi.config import Config

    monkeypatch.setattr(
        appmod,
        "load_config",
        lambda: Config(data_dir=tmp_path / "data", password="hunter2"),
    )
    calls: list = []

    class _StubQueue:
        def recover(self):
            return 0

        def start(self, _concurrency):
            pass

    class _StubScheduler:
        def start(self):
            pass

    class _StubPoller:
        def start(self):
            pass

        def stop(self):
            pass

        def join(self, timeout=None):
            pass

    class _StubApp:
        config = {
            "JALEBI_QUEUE": _StubQueue(),
            "JALEBI_SCREENING": _StubScheduler(),
            "JALEBI_POLLER": _StubPoller(),
        }

        def app_context(self):
            from contextlib import nullcontext

            return nullcontext()

        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(appmod, "create_app", lambda _config: _StubApp())

    class _StubSession:
        def close(self):
            pass

    monkeypatch.setattr(appmod.db, "Session", lambda: _StubSession())
    monkeypatch.setattr(appmod.settings, "get_setting", lambda _s, _k: 0)
    monkeypatch.setattr(appmod.artifacts, "prune_artifacts", lambda *_a, **_k: 0)
    appmod.main()
    assert calls, "app.run was never reached with a password set"


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
