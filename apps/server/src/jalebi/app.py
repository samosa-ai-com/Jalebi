"""Flask application factory and CLI entrypoint."""

import hmac
import logging
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, current_app, g, jsonify, request, send_from_directory
from flask.typing import ResponseReturnValue

from jalebi import artifacts, db, masking, notify, secrets, settings
from jalebi.adapters import get_adapter
from jalebi.config import Config, load_config, repo_root
from jalebi.queue import TaskQueue
from jalebi.routes.catalog import bp as catalog_bp
from jalebi.routes.envvars import bp as envvars_bp
from jalebi.routes.github import bp as github_bp
from jalebi.routes.repos import bp as repos_bp
from jalebi.routes.screening import bp as screening_bp
from jalebi.routes.tasks import bp as tasks_bp
from jalebi.routes.triggers import bp as triggers_bp
from jalebi.routes.webhooks import bp as webhooks_bp
from jalebi.screening import ScreeningScheduler

logger = logging.getLogger(__name__)

WEB_DIST = repo_root() / "apps" / "web" / "dist"

ALLOWED_AGENT_CLIS = ("opencode",)


def _valid_secret_patterns(value: object) -> bool:
    """Every entry must be a str and a compilable regex (surface errors to the user)."""
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        return False
    for pattern in value:
        try:
            re.compile(pattern)
        except re.error:
            return False
    return True


def _valid_retry_policy(v) -> bool:
    """retry_policy: {auto_retry, continue_prompt?, timeout_multiplier?, max_timeout_minutes?}.

    Accepts the legacy ``{"auto_retry": bool}`` shape too; new keys are optional.
    ``timeout_multiplier`` may be fractional (matches the consumer, which accepts
    int/float); ``max_timeout_minutes`` is an int (minutes).
    """
    if not isinstance(v, dict) or not isinstance(v.get("auto_retry"), bool):
        return False
    if "continue_prompt" in v and not isinstance(v["continue_prompt"], str):
        return False
    if "timeout_multiplier" in v and not (
        isinstance(v["timeout_multiplier"], (int, float))
        and v["timeout_multiplier"] >= 1
    ):
        return False
    if "max_timeout_minutes" in v and not (
        isinstance(v["max_timeout_minutes"], int) and v["max_timeout_minutes"] >= 1
    ):
        return False
    return True


_SETTING_VALIDATORS = {
    "concurrency": lambda v: isinstance(v, int) and 0 <= v <= 64,
    "auto_publish": lambda v: isinstance(v, bool),
    "default_timeout_minutes": lambda v: isinstance(v, int) and v >= 1,
    # Merged ntfy endpoint: bare topic or an http(s) URL. ntfy_url is gone.
    "ntfy_topic": lambda v: isinstance(v, str) and (
        v == "" or v.startswith(("http://", "https://")) or ("/" not in v and " " not in v)
    ),
    "retry_policy": _valid_retry_policy,
    "stall_timeout_seconds": lambda v: isinstance(v, int) and v >= 60,
    "secret_patterns": _valid_secret_patterns,
    "artifact_ttl_days": lambda v: isinstance(v, int) and v >= 1,
    "agent_cli": lambda v: v in ALLOWED_AGENT_CLIS,
    "notify_on_done": lambda v: isinstance(v, bool),
    "notify_on_failed": lambda v: isinstance(v, bool),
    "notify_on_progress": lambda v: isinstance(v, bool),
    "notify_on_needs_approval": lambda v: isinstance(v, bool),
    "notify_progress_interval_minutes": lambda v: isinstance(v, int) and v >= 1,
    "webhook_url": lambda v: isinstance(v, str) and (
        v == "" or v.startswith(("http://", "https://"))
    ),
    "webhook_secret": lambda v: isinstance(v, str),
}


def _serve_spa(web_dist: Path, filename: str) -> ResponseReturnValue:
    """Serve a built SPA file, falling back to index.html (client-side routing)."""
    if not (web_dist / "index.html").is_file():
        return jsonify({"error": "web build missing; run: npm run build"}), 503
    if (web_dist / filename).is_file():
        return send_from_directory(web_dist, filename)
    return send_from_directory(web_dist, "index.html")


def _basic_auth_gate() -> ResponseReturnValue | None:
    """Require Basic auth on everything except /api/health when a password is set.

    PRD §F13: an optional UI password (``JALEBI_PASSWORD``) protects the app if it
    is ever exposed via a tunnel. Localhost-only installs leave it unset → no gate.
    The username is ignored (any user with the password passes); the password is
    compared in constant time to avoid a timing side channel.

    Wrong-password attempts are pushed to the configured ntfy topic (throttled
    per client so a brute-force scan can't flood the channel).
    """
    password = current_app.config["JALEBI_CONFIG"].password
    if not password:
        return None
    if request.path == "/api/health":
        return None
    # GitHub webhooks cannot send Basic auth — the X-Hub-Signature-256 secret is
    # the webhook's own authentication (verified in routes/webhooks.py). Exempt
    # the listener so a password-protected (possibly tunneled) install still
    # receives deliveries.
    if request.method == "POST" and request.path == "/webhook":
        return None
    auth = request.authorization
    if (
        auth is not None
        and auth.password is not None
        and hmac.compare_digest(auth.password.encode(), password.encode())
    ):
        return None
    _notify_failed_login()
    return (
        jsonify({"error": "authentication required"}),
        401,
        {"WWW-Authenticate": 'Basic realm="Jalebi"'},
    )


# -- failed-login ntfy push (throttled) -----------------------------------

# One push per client per window, so a brute-force scan can't spam the channel.
_FAILED_LOGIN_WINDOW_SECONDS = 60
_failed_login_pushes: dict[str, float] = {}
_failed_login_lock = threading.Lock()


def _notify_failed_login() -> None:
    """Best-effort ntfy push for a wrong-password attempt (never blocks or raises).

    Runs on a daemon thread so a dead/slow ntfy server can never delay the 401
    response (or let a scan tie up request threads); the push is dropped if the
    thread is skipped. The attempted username is attacker-controlled input, so it
    is masked before sending; the client is identified by IP (X-Forwarded-For is
    not trusted — anyone can set it, and only the tunnel edge sees the real peer).
    """
    config: Config = current_app.config["JALEBI_CONFIG"]
    if not config.password:
        return
    client = request.remote_addr or "unknown"
    now = time.monotonic()
    with _failed_login_lock:
        last = _failed_login_pushes.get(client)
        if last is not None and now - last < _FAILED_LOGIN_WINDOW_SECONDS:
            return
        _failed_login_pushes[client] = now
        # Bound the map: a tunneled install sees few distinct peers, but a scan
        # can fake many; drop stale entries once we exceed a sane size.
        if len(_failed_login_pushes) > 512:
            expired = [
                k
                for k, t in _failed_login_pushes.items()
                if now - t >= _FAILED_LOGIN_WINDOW_SECONDS
            ]
            for k in expired:
                _failed_login_pushes.pop(k, None)

    attempted_user = (request.authorization.username if request.authorization else "") or ""
    has_credentials = (
        request.authorization is not None and request.authorization.password is not None
    )
    app = current_app._get_current_object()

    def _push() -> None:
        with app.app_context():
            try:
                session = db.get_session()
                raw_patterns = settings.get_setting(session, "secret_patterns") or []
                patterns = (
                    [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
                )
                masker = masking.build_masker(secrets.all_token_values(config), patterns)
                if has_credentials:
                    username = masker(attempted_user) if attempted_user else "(none)"
                    detail = (
                        f"Wrong password received from **{client}** "
                        f"(attempted user: `{username}`)."
                    )
                else:
                    detail = (
                        f"Unauthorized request from **{client}** "
                        "(no credentials supplied)."
                    )
                notify.send(
                    session,
                    "Jalebi: failed login attempt",
                    detail,
                    tags="warning",
                    priority=3,
                    click=f"http://127.0.0.1:{config.port}/",
                    masker=masker,
                )
            except Exception:
                logger.exception("failed-login ntfy push errored")  # pragma: no cover

    threading.Thread(target=_push, daemon=True).start()


def create_app(config: Config | None = None) -> Flask:
    """Create and configure the Jalebi Flask application."""
    if config is None:
        config = load_config()
    config.ensure_dirs()

    app = Flask(__name__)
    app.config["JALEBI_CONFIG"] = config

    db.init_db(config.db_url)
    db.run_migrations(config.db_url)

    # Persist every settings default that has no stored row yet, so all
    # configurations are explicit in the DB and survive restarts (PRD goal:
    # nothing is held in memory). Existing user values are never overwritten.
    with app.app_context():
        session = db.get_session()
        try:
            settings.seed_defaults(session)
        finally:
            session.close()

    app.config["JALEBI_QUEUE"] = TaskQueue(config)
    app.config["JALEBI_SCREENING"] = ScreeningScheduler(config)

    app.register_blueprint(github_bp)
    app.register_blueprint(repos_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(envvars_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(triggers_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(screening_bp)

    @app.teardown_appcontext
    def close_session(_exc) -> None:
        session = g.pop("_db_session", None)
        if session is not None:
            session.close()

    app.before_request(_basic_auth_gate)

    @app.get("/api/health")
    def health() -> Response:
        return jsonify({"status": "ok"})

    @app.get("/api/settings")
    def get_settings() -> Response:
        session = db.get_session()
        values = {key: settings.get_setting(session, key) for key in settings.SETTING_KEYS}
        # The webhook HMAC secret is write-only: never return the value, only
        # whether one is set (the UI renders it as a masked placeholder).
        if values.get("webhook_secret"):
            values["webhook_secret"] = settings.SECRET_MASK
        return jsonify(values)

    @app.get("/api/models")
    def list_models() -> ResponseReturnValue:
        """Models available from the configured agent CLI (for the task form)."""
        session = db.get_session()
        cli = str(settings.get_setting(session, "agent_cli") or "opencode")
        try:
            models = get_adapter(cli).list_models()
        except Exception:
            models = []
        return jsonify({"cli": cli, "models": models})

    @app.post("/api/settings")
    def update_settings() -> ResponseReturnValue:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "expected a JSON object"}), 400
        key = payload.get("key")
        if key not in settings.SETTING_KEYS:
            return jsonify({"error": f"unknown setting key: {key}"}), 400
        value = payload.get("value")
        validator = _SETTING_VALIDATORS.get(key)
        if validator is not None and not validator(value):
            return jsonify({"error": f"invalid value for {key}"}), 400
        session = db.get_session()
        # A write-only secret that was fetched masked and re-submitted unchanged
        # must not clobber the stored value; empty clears it.
        if key == "webhook_secret" and value == settings.SECRET_MASK:
            value = settings.get_setting(session, "webhook_secret")
        settings.set_setting(session, key, value)
        if key == "concurrency" and isinstance(value, int):
            current_app.config["JALEBI_QUEUE"].set_concurrency(value)
        response_value = settings.SECRET_MASK if key == "webhook_secret" and value else value
        return jsonify({key: response_value}), 200

    @app.post("/api/notify/test")
    def notify_test() -> ResponseReturnValue:
        """Send a test push to the configured ntfy topic; returns ok/error."""
        session = db.get_session()
        raw_patterns = settings.get_setting(session, "secret_patterns") or []
        patterns = [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
        masker = masking.build_masker(
            secrets.all_token_values(current_app.config["JALEBI_CONFIG"]), patterns
        )
        ok, error = notify.send(
            session,
            "Jalebi test notification",
            "**If you can read this**, your ntfy configuration works.\n\n"
            "Markdown, priorities, tags and a tap-action are enabled.",
            tags=notify.TAGS_OK,
            click=f"http://127.0.0.1:{config.port}/",
            actions=[
                {
                    "action": "view",
                    "label": "Open Jalebi",
                    "url": f"http://127.0.0.1:{config.port}/",
                }
            ],
            masker=masker,
        )
        if not ok:
            return jsonify({"ok": False, "error": error or "notification failed"}), 400
        return jsonify({"ok": True})

    # SPA: serve the built React app (index.html + assets) so the UI lives on the
    # same origin as the API. Werkzeug prioritizes the literal /api routes above
    # this catch-all.
    @app.get("/")
    def spa_index() -> ResponseReturnValue:
        return _serve_spa(WEB_DIST, "index.html")

    @app.get("/api/<path:rest>")
    def api_not_found(rest: str) -> ResponseReturnValue:
        # Unknown /api/* paths must 404 as JSON, not fall through to the SPA.
        return jsonify({"error": f"no such route: /api/{rest}"}), 404

    @app.get("/<path:filename>")
    def spa_files(filename: str) -> ResponseReturnValue:
        return _serve_spa(WEB_DIST, filename)

    return app


def main() -> None:
    """Run the development server, bound to localhost only."""
    config = load_config()
    app = create_app(config)
    queue = app.config["JALEBI_QUEUE"]
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
    recovered = queue.recover()
    if recovered:
        logger.info("queue recovery: %s interrupted/requeued item(s)", recovered)
    queue.start(concurrency)
    scheduler = app.config["JALEBI_SCREENING"]
    scheduler.start()
    logger.info("screening scheduler started")
    app.run(host=config.host, port=config.port, threaded=True)


if __name__ == "__main__":
    main()
