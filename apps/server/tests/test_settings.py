from sqlalchemy.orm import Session as OrmSession

from jalebi.db import Session
from jalebi.settings import DEFAULTS, get_setting, set_setting


def test_defaults_returned_when_unset(session: OrmSession) -> None:
    assert get_setting(session, "concurrency") == 4
    assert get_setting(session, "auto_publish") is True
    assert get_setting(session, "default_timeout_minutes") == 30
    assert get_setting(session, "artifact_ttl_days") == 7
    assert get_setting(session, "ntfy_topic") == ""
    assert get_setting(session, "secret_patterns") == []
    assert get_setting(session, "retry_policy") == {"auto_retry": False}
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


def test_all_defaults_valid_json() -> None:
    import json

    for key, value in DEFAULTS.items():
        assert json.loads(json.dumps(value)) == value
