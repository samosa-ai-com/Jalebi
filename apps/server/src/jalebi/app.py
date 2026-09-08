"""Flask application factory and CLI entrypoint."""

import hmac
import logging
import re
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, Response, current_app, g, jsonify, request, send_from_directory
from flask.typing import ResponseReturnValue

from jalebi import artifacts, clock, db, ide, masking, notify, secrets, seed_catalog, settings
from jalebi.adapters import ADAPTERS, available_adapters, get_adapter
from jalebi.config import Config, load_config, repo_root
from jalebi.poller import Poller
from jalebi.queue import TaskQueue
from jalebi.routes.catalog import bp as catalog_bp
from jalebi.routes.data import bp as data_bp
from jalebi.routes.envvars import bp as envvars_bp
from jalebi.routes.github import bp as github_bp
from jalebi.routes.repos import bp as repos_bp
from jalebi.routes.screening import bp as screening_bp
from jalebi.routes.skills import bp as skills_bp
from jalebi.routes.tasks import bp as tasks_bp
from jalebi.routes.triggers import bp as triggers_bp
from jalebi.routes.webhooks import bp as webhooks_bp
from jalebi.screening import ScreeningScheduler

logger = logging.getLogger(__name__)

WEB_DIST = repo_root() / "apps" / "web" / "dist"

# Derived from the adapter registry (single source of truth) so the setting
# allow-list can never drift from the implemented adapters.
ALLOWED_AGENT_CLIS = tuple(available_adapters())


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
    """retry_policy: {auto_retry, continue_prompt?, timeout_multiplier?,
    max_timeout_minutes?, max_attempts?, non_retryable_patterns?}.

    Accepts the legacy ``{"auto_retry": bool}`` shape too; new keys are optional.
    ``timeout_multiplier`` may be fractional (matches the consumer, which accepts
    int/float); ``max_timeout_minutes`` is an int (minutes); ``max_attempts``
    caps total auto-recovery attempts per task (>= 1); ``non_retryable_patterns``
    is a list of case-insensitive substrings that fail immediately.
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
    if "max_attempts" in v and not (
        isinstance(v["max_attempts"], int) and v["max_attempts"] >= 1
    ):
        return False
    if "non_retryable_patterns" in v and not (
        isinstance(v["non_retryable_patterns"], list)
        and all(isinstance(p, str) for p in v["non_retryable_patterns"])
    ):
        return False
    return True


def _valid_timezone(v) -> bool:
    """``timezone`` setting: ``"local"`` (system zone) or a valid IANA name."""
    if not isinstance(v, str):
        return False
    name = v.strip() or "local"
    if name == "local":
        return True
    try:
        ZoneInfo(name)
        return True
    except ZoneInfoNotFoundError:
        return False


COMMON_TIMEZONES = [
    "Pacific/Honolulu",
    "America/Anchorage",
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "America/Sao_Paulo",
    "Atlantic/Azores",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "Europe/Moscow",
    "Africa/Cairo",
    "Asia/Dubai",
    "Asia/Karachi",
    "Asia/Kolkata",
    "Asia/Dhaka",
    "Asia/Bangkok",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Shanghai",
    "Australia/Perth",
    "Australia/Sydney",
    "Pacific/Auckland",
    "UTC",
]


def _valid_adapter_model_lists(v) -> bool:
    """adapter_model_lists: {cli: [model names]} overriding each adapter's curated list.

    Keys must be known adapters; values must be non-empty-str lists.
    """
    if not isinstance(v, dict):
        return False
    return all(
        isinstance(cli, str)
        and cli in ALLOWED_AGENT_CLIS
        and isinstance(models, list)
        and all(isinstance(m, str) and m.strip() for m in models)
        for cli, models in v.items()
    )


def _valid_enabled_backends(v) -> bool:
    """enabled_backends: non-empty subset of the registered adapters."""
    return (
        isinstance(v, list)
        and len(v) > 0
        and all(isinstance(c, str) and c in ALLOWED_AGENT_CLIS for c in v)
    )


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
    "default_backend": lambda v: v in ALLOWED_AGENT_CLIS,
    "default_model": lambda v: isinstance(v, str) and bool(v.strip()),
    "enabled_backends": _valid_enabled_backends,
    "adapter_model_lists": _valid_adapter_model_lists,
    "notify_on_done": lambda v: isinstance(v, bool),
    "notify_on_failed": lambda v: isinstance(v, bool),
    "notify_on_progress": lambda v: isinstance(v, bool),
    "notify_on_needs_approval": lambda v: isinstance(v, bool),
    "notify_progress_interval_minutes": lambda v: isinstance(v, int) and v >= 1,
    "webhook_url": lambda v: isinstance(v, str) and (
        v == "" or v.startswith(("http://", "https://"))
    ),
    "webhook_secret": lambda v: isinstance(v, str),
    "timezone": _valid_timezone,
    # Phase 4 T6 — IDE connector.
    "ide_command": ide.validate_ide_command,
    "ide_name": lambda v: isinstance(v, str),
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
    # /webhook is auth-exempt by design (the HMAC signature is its auth), so
    # cap request bodies: oversized deliveries 413 instead of exhausting memory.
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    db.init_db(config.db_url)
    db.run_migrations(config.db_url)

    # Persist every settings default that has no stored row yet, so all
    # configurations are explicit in the DB and survive restarts (PRD goal:
    # nothing is held in memory). Existing user values are never overwritten.
    with app.app_context():
        session = db.get_session()
        try:
            settings.seed_defaults(session)
            # Preloaded skill/agent library (version-gated, insert-missing
            # only — owner edits and deletions are never overwritten).
            seed_catalog.seed_catalog(session)
            # Sync the app wall clock to the configured timezone (default: the
            # machine's local zone) so the screening scheduler's cron matching
            # and every timestamp follow it.
            clock.set_zone(str(settings.get_setting(session, "timezone") or ""))
        finally:
            session.close()

    # NOTE: the factory must be the plain sessionmaker, NOT db.get_session:
    # event persistence runs on worker threads with no Flask app context, and
    # get_session() is request-scoped (Flask `g`) — it raises RuntimeError
    # outside a request. db.Session() is context-free.
    app.config["JALEBI_QUEUE"] = TaskQueue(
        config, db_session_factory=db.Session
    )
    app.config["JALEBI_SCREENING"] = ScreeningScheduler(config)
    app.config["JALEBI_POLLER"] = Poller(
        config, queue=app.config["JALEBI_QUEUE"]
    )  # Phase 4 T2.1 — default OFF; queue wired for the T4.2 auto-nudge

    app.register_blueprint(github_bp)
    app.register_blueprint(repos_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(envvars_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(skills_bp)
    app.register_blueprint(data_bp)
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
        """Models available from an agent CLI (for the model dropdowns).

        The backend defaults to the ``default_backend`` setting; a ``?cli=<backend>``
        query param overrides it (used by the Settings default-model dropdown and
        the Screenings/Agents/Tasks forms so the model list follows the backend
        selected *in that form*). An ``adapter_model_lists`` setting entry for
        the requested cli wins over the adapter's own ``list_models()``;
        otherwise the adapter is asked (missing CLI / unknown backend → empty
        list).
        """
        session = db.get_session()
        requested = (request.args.get("cli") or "").strip()
        cli = requested or str(settings.get_setting(session, "default_backend") or "opencode")
        overrides = settings.get_setting(session, "adapter_model_lists") or {}
        # ``.get`` returns None only when the key is absent, so an explicit empty
        # list is authoritative (an owner can clear/disable the dropdown).
        override_models = overrides.get(cli) if isinstance(overrides, dict) else None
        if override_models is not None:
            return jsonify({"cli": cli, "models": override_models})
        try:
            models = get_adapter(cli).list_models()
        except Exception:
            models = []
        return jsonify({"cli": cli, "models": models})

    @app.get("/api/timezones")
    def list_timezones() -> ResponseReturnValue:
        """IANA timezones for the Settings dropdown (plus ``local`` first)."""
        from zoneinfo import available_timezones

        try:
            all_zones = sorted(available_timezones())
        except Exception:
            all_zones = []
        return jsonify({"local": "local", "common": COMMON_TIMEZONES, "all": all_zones})

    @app.get("/api/backends")
    def list_backends() -> ResponseReturnValue:
        """Backends the app may use: registry order with enabled flags."""
        session = db.get_session()
        enabled = settings.get_setting(session, "enabled_backends")
        if not isinstance(enabled, list):
            enabled = list(ADAPTERS)
        order = list(ADAPTERS)
        default = str(settings.get_setting(session, "default_backend") or "opencode")
        return jsonify(
            {
                "backends": order,
                "enabled": [c for c in order if c in enabled],
                "default": default,
            }
        )

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
        # Cross-field invariant: the default backend must stay enabled, and at
        # least one backend must stay enabled (shape already validated above).
        if key == "enabled_backends" and isinstance(value, list):
            default_backend = str(settings.get_setting(session, "default_backend"))
            if default_backend not in value:
                return (
                    jsonify(
                        {
                            "error": (
                                f"cannot disable {default_backend}: it is the default "
                                "backend (change the default first)"
                            )
                        }
                    ),
                    400,
                )
        if key == "default_backend" and isinstance(value, str):
            enabled = settings.get_setting(session, "enabled_backends") or []
            if not isinstance(enabled, list) or value not in enabled:
                return (
                    jsonify({"error": f"{value} is not an enabled backend"}),
                    400,
                )
        # A write-only secret that was fetched masked and re-submitted unchanged
        # must not clobber the stored value; empty clears it.
        if key == "webhook_secret" and value == settings.SECRET_MASK:
            value = settings.get_setting(session, "webhook_secret")
        settings.set_setting(session, key, value)
        if key == "concurrency" and isinstance(value, int):
            current_app.config["JALEBI_QUEUE"].set_concurrency(value)
        if key == "timezone":
            # The wall clock is a module global; re-sync it so the new zone
            # applies immediately (scheduler cron matching + timestamps).
            clock.set_zone(str(value or ""))
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

    # ---- Phase 4 T6 — IDE connector endpoints -----------------------------

    @app.get("/api/ide/status")
    def ide_status() -> ResponseReturnValue:
        """What's currently configured + whether the command resolves."""
        session = db.get_session()
        return jsonify(ide.ide_status(session))

    @app.get("/api/ide/detect")
    def ide_detect() -> ResponseReturnValue:
        """Probe the whitelist for installed IDEs on PATH."""
        all_detected = ide.detect_all_ides()
        first = all_detected[0] if all_detected else None
        return jsonify({
            "detected": all_detected,
            "command": first["command"] if first else "",
            "name": first["name"] if first else "",
        })

    @app.post("/api/ide/test")
    def ide_test() -> ResponseReturnValue:
        """Open the configured IDE on a scratch dir to confirm it launches."""
        session = db.get_session()
        ok, error = ide.test_open(session)
        if not ok:
            return jsonify({"ok": False, "error": error}), 400
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

    @app.post("/api/<path:rest>")
    @app.put("/api/<path:rest>")
    @app.patch("/api/<path:rest>")
    @app.delete("/api/<path:rest>")
    def api_not_found_write(rest: str) -> ResponseReturnValue:
        # Same JSON 404 for writes — otherwise an unknown POST answers HTML
        # 405 (the GET catch-all claims the path for another method).
        return jsonify({"error": f"no such route: /api/{rest}"}), 404

    @app.errorhandler(413)
    def api_too_large(_exc) -> ResponseReturnValue:
        return jsonify({"error": "request body exceeds 10 MB"}), 413

    @app.get("/<path:filename>")
    def spa_files(filename: str) -> ResponseReturnValue:
        return _serve_spa(WEB_DIST, filename)

    return app


def main() -> None:
    """Run the development server on the configured host (0.0.0.0 by default)."""
    config = load_config()
    if not config.password:
        logger.error(
            "JALEBI_PASSWORD (or OPENCODE_SERVER_PASSWORD) is required — "
            "refusing to start without a UI password on %s",
            config.host,
        )
        raise SystemExit(2)
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
            # Timeline data (task_events) is never auto-deleted by design —
            # it grows until pruned manually via Settings → Data management.
        finally:
            session.close()
    recovered = queue.recover()
    if recovered:
        logger.info("queue recovery: %s interrupted/requeued item(s)", recovered)
    queue.start(concurrency)
    scheduler = app.config["JALEBI_SCREENING"]
    scheduler.start()
    logger.info("screening scheduler started")
    poller = app.config["JALEBI_POLLER"]
    poller.start()
    logger.info("pr polling observer started (default off; enable per repo)")
    try:
        app.run(host=config.host, port=config.port, threaded=True)
    finally:
        poller.stop()
        poller.join(timeout=2)


if __name__ == "__main__":
    main()
