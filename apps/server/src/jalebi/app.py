"""Flask application factory and CLI entrypoint."""

import logging
from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_from_directory
from flask.typing import ResponseReturnValue

from jalebi import artifacts, db, secrets, settings
from jalebi.config import Config, load_config, repo_root
from jalebi.queue import TaskQueue
from jalebi.routes.github import bp as github_bp
from jalebi.routes.repos import bp as repos_bp
from jalebi.routes.tasks import bp as tasks_bp

logger = logging.getLogger(__name__)

WEB_DIST = repo_root() / "apps" / "web" / "dist"


def _serve_spa(web_dist: Path, filename: str) -> ResponseReturnValue:
    """Serve a built SPA file, falling back to index.html (client-side routing)."""
    if not (web_dist / "index.html").is_file():
        return jsonify({"error": "web build missing; run: npm run build"}), 503
    if (web_dist / filename).is_file():
        return send_from_directory(web_dist, filename)
    return send_from_directory(web_dist, "index.html")


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

    app.config["JALEBI_QUEUE"] = TaskQueue(config)

    app.register_blueprint(github_bp)
    app.register_blueprint(repos_bp)
    app.register_blueprint(tasks_bp)

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

    # SPA: serve the built React app (index.html + assets) so the UI lives on the
    # same origin as the API. Werkzeug prioritizes the literal /api routes above
    # this catch-all.
    @app.get("/")
    def spa_index() -> ResponseReturnValue:
        return _serve_spa(WEB_DIST, "index.html")

    @app.get("/<path:filename>")
    def spa_files(filename: str) -> ResponseReturnValue:
        return _serve_spa(WEB_DIST, filename)

    return app


def main() -> None:
    """Run the development server, bound to localhost only."""
    config = load_config()
    app = create_app(config)
    with app.app_context():
        session = db.Session()
        try:
            raw_concurrency = settings.get_setting(session, "concurrency") or 0
            concurrency = raw_concurrency if isinstance(raw_concurrency, int) else 0
            raw_ttl = settings.get_setting(session, "artifact_ttl_days") or 7
            ttl = raw_ttl if isinstance(raw_ttl, int) and raw_ttl > 0 else 7
            pruned = artifacts.prune_artifacts(session, config.data_dir, ttl)
            if pruned:
                logger.info("pruned %s expired artifact(s)", pruned)
        finally:
            session.close()
    app.config["JALEBI_QUEUE"].start(concurrency)
    app.run(host=config.host, port=config.port, threaded=True)


if __name__ == "__main__":
    main()
