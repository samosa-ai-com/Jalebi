"""Flask application factory and CLI entrypoint."""

from flask import Flask, Response, g, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import db, secrets, settings
from jalebi.config import Config, load_config
from jalebi.routes.github import bp as github_bp
from jalebi.routes.repos import bp as repos_bp


def create_app(config: Config | None = None) -> Flask:
    """Create and configure the Jalebi Flask application."""
    if config is None:
        config = load_config()
    config.ensure_dirs()
    secrets.persist_env_github_token(config)

    app = Flask(__name__)
    app.config["JALEBI_CONFIG"] = config

    db.init_db(config.db_url)
    db.run_migrations(config.db_url)

    app.register_blueprint(github_bp)
    app.register_blueprint(repos_bp)

    @app.teardown_appcontext
    def close_session(_exc) -> None:
        session = g.pop("_db_session", None)
        if session is not None:
            session.close()

    @app.get("/api/health")
    def health() -> Response:
        return jsonify({"status": "ok"})

    @app.get("/api/settings")
    def get_settings() -> Response:
        session = db.get_session()
        return jsonify({key: settings.get_setting(session, key) for key in settings.SETTING_KEYS})

    @app.post("/api/settings")
    def update_settings() -> ResponseReturnValue:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "expected a JSON object"}), 400
        key = payload.get("key")
        if key not in settings.SETTING_KEYS:
            return jsonify({"error": f"unknown setting key: {key}"}), 400
        session = db.get_session()
        settings.set_setting(session, key, payload.get("value"))
        return jsonify({key: settings.get_setting(session, key)}), 200

    return app


def main() -> None:
    """Run the development server, bound to localhost only."""
    config = load_config()
    app = create_app(config)
    app.run(host=config.host, port=config.port, threaded=True)


if __name__ == "__main__":
    main()
