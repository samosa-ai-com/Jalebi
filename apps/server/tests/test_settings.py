from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from jalebi.db import Session, Setting
from jalebi.settings import DEFAULTS, get_setting, seed_defaults, set_setting


def test_defaults_returned_when_unset(session: OrmSession) -> None:
    assert get_setting(session, "concurrency") == 4
    assert get_setting(session, "auto_publish") is True
    assert get_setting(session, "default_timeout_minutes") == 60
    assert get_setting(session, "artifact_ttl_days") == 7
    assert get_setting(session, "ntfy_topic") == ""
    assert get_setting(session, "secret_patterns") == []
    assert get_setting(session, "retry_policy") == {
        "auto_retry": True,
        "continue_prompt": "continue",
        "timeout_multiplier": 2,
        "max_timeout_minutes": 180,
        "max_attempts": 3,
        "non_retryable_patterns": [
            "model not found",
            "invalid model",
            "unknown model",
            "authentication failed",
            "unauthorized",
            "no GitHub token",
            "has no resumable session",
        ],
    }
    assert get_setting(session, "stall_timeout_seconds") == 600
    assert get_setting(session, "unknown_key") is None


def test_set_and_get_roundtrip(session: OrmSession) -> None:
    set_setting(session, "concurrency", 8)
    assert get_setting(session, "concurrency") == 8

    set_setting(session, "secret_patterns", ["ghp_", "AKIA"])
    assert get_setting(session, "secret_patterns") == ["ghp_", "AKIA"]

    set_setting(session, "auto_publish", False)
    assert get_setting(session, "auto_publish") is False


def test_override_persists_across_sessions(session: OrmSession) -> None:
    set_setting(session, "concurrency", 2)
    session.close()

    fresh = Session()
    try:
        assert get_setting(fresh, "concurrency") == 2
    finally:
        fresh.close()


def test_seed_defaults_materializes_all_keys(engine, session: OrmSession) -> None:
    """seed_defaults inserts a stored row for EVERY settings key, so nothing
    depends on code-side defaults at runtime and the config is inspectable."""
    # Clear the table first (the app fixture already seeds on startup).
    with engine.begin() as conn:
        conn.execute(delete(Setting))
    session.expire_all()
    assert seed_defaults(session) == len(DEFAULTS)
    stored = {
        row.key: row.value for row in session.execute(select(Setting)).scalars()
    }
    assert set(stored) == set(DEFAULTS)
    # Idempotent: a second seed adds nothing.
    assert seed_defaults(session) == 0


def test_app_startup_seeds_all_settings(app) -> None:
    """create_app seeds every settings key at startup, so all configurations
    are materialized in the DB (persistent and inspectable)."""
    with app.app_context():
        from jalebi import db

        s = db.Session()
        try:
            stored = {row.key for row in s.execute(select(Setting)).scalars()}
            assert set(DEFAULTS) <= stored
        finally:
            s.close()


def test_seed_defaults_never_overwrites_user_values(session: OrmSession) -> None:
    set_setting(session, "concurrency", 2)
    set_setting(session, "ntfy_topic", "https://ntfy.example.com/room")
    seed_defaults(session)
    assert get_setting(session, "concurrency") == 2
    assert get_setting(session, "ntfy_topic") == "https://ntfy.example.com/room"
    # All other keys were seeded too.
    stored = {row.key for row in session.execute(select(Setting)).scalars()}
    assert set(DEFAULTS) <= stored


def test_all_settings_survive_full_restart(session: OrmSession) -> None:
    """A simulated app restart (fresh engine/session) reads every stored
    configuration back — nothing 'washes away'."""
    seed_defaults(session)
    set_setting(session, "concurrency", 3)
    set_setting(session, "auto_publish", False)
    set_setting(session, "ntfy_topic", "https://ntfy.example.com/room")
    set_setting(session, "default_timeout_minutes", 45)
    set_setting(session, "notify_progress_interval_minutes", 15)
    set_setting(session, "retry_policy", {"auto_retry": True})
    set_setting(session, "secret_patterns", ["AKIA[0-9A-Z]{16}"])
    session.close()

    # Simulate a restart: a brand-new session from the same engine.
    fresh = Session()
    try:
        assert get_setting(fresh, "concurrency") == 3
        assert get_setting(fresh, "auto_publish") is False
        assert get_setting(fresh, "ntfy_topic") == "https://ntfy.example.com/room"
        assert get_setting(fresh, "default_timeout_minutes") == 45
        assert get_setting(fresh, "notify_progress_interval_minutes") == 15
        assert get_setting(fresh, "retry_policy") == {"auto_retry": True}
        assert get_setting(fresh, "secret_patterns") == ["AKIA[0-9A-Z]{16}"]
        # Untouched defaults are still materialized rows.
        assert get_setting(fresh, "default_backend") == "opencode"
        assert get_setting(fresh, "default_model") == ""
        assert get_setting(fresh, "notify_on_done") is True
    finally:
        fresh.close()


def test_all_defaults_valid_json() -> None:
    import json

    for key, value in DEFAULTS.items():
        assert json.loads(json.dumps(value)) == value
