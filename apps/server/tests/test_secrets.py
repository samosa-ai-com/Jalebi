import pytest

from jalebi import secrets
from jalebi.config import Config


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(host="127.0.0.1", port=3456, data_dir=tmp_path / "data")


def test_store_and_load_roundtrip(cfg: Config) -> None:
    secrets.store_secret(cfg, "github_token", "ghp_secret")
    assert secrets.load_secret(cfg, "github_token") == "ghp_secret"


def test_file_permissions_0600(cfg: Config) -> None:
    secrets.store_secret(cfg, "k", "v")
    mode = secrets.secrets_path(cfg).stat().st_mode & 0o777
    assert mode == 0o600


def test_load_secret_missing_file(cfg: Config) -> None:
    assert secrets.load_secret(cfg, "github_token") is None


def test_named_tokens_crud(cfg: Config) -> None:
    secrets.add_github_token(cfg, "work", "ghp_work")
    secrets.add_github_token(cfg, "personal", "ghp_personal")
    assert secrets.token_names(cfg) == ["work", "personal"]
    assert secrets.get_named_token(cfg, "work") == "ghp_work"
    secrets.remove_github_token(cfg, "work")
    assert secrets.token_names(cfg) == ["personal"]


def test_add_named_token_replaces_same_name(cfg: Config) -> None:
    secrets.add_github_token(cfg, "work", "ghp_old")
    secrets.add_github_token(cfg, "work", "ghp_new")
    names = secrets.token_names(cfg)
    assert names == ["work"]
    assert secrets.get_named_token(cfg, "work") == "ghp_new"


def test_resolve_named_token(cfg: Config, monkeypatch) -> None:
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.add_github_token(cfg, "work", "ghp_work")
    assert secrets.resolve_token(cfg, "work") == "ghp_work"


def test_resolve_unknown_or_none_is_none(cfg: Config, monkeypatch) -> None:
    """Resolution is strict: an unknown or missing account never falls back to
    any other token (no primary/default/first-vault)."""
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.store_secret(cfg, "github_token", "ghp_primary")
    secrets.add_github_token(cfg, "work", "ghp_work")
    # Unknown name → None (NOT the primary, NOT the first vault entry).
    assert secrets.resolve_token(cfg, "missing") is None
    # None/empty → None.
    assert secrets.resolve_token(cfg, None) is None
    assert secrets.resolve_token(cfg, "") is None
    # The named token still resolves exactly.
    assert secrets.resolve_token(cfg, "work") == "ghp_work"


def test_resolve_ignores_env_token(cfg: Config, monkeypatch) -> None:
    """The env/bootstrap token is masked but never used for resolution."""
    monkeypatch.setenv(secrets.ENV_GITHUB_TOKEN, "ghp_env")
    secrets.add_github_token(cfg, "work", "ghp_work")
    assert secrets.resolve_token(cfg, "work") == "ghp_work"
    assert secrets.resolve_token(cfg, None) is None


def test_all_token_values_dedup(cfg: Config, monkeypatch) -> None:
    monkeypatch.setenv(secrets.ENV_GITHUB_TOKEN, "ghp_env")
    secrets.store_secret(cfg, "github_token", "ghp_primary")
    secrets.add_github_token(cfg, "a", "ghp_a")
    secrets.add_github_token(cfg, "b", "ghp_a")  # duplicate value
    values = secrets.all_token_values(cfg)
    assert values.count("ghp_a") == 1
    assert "ghp_env" in values
    assert "ghp_primary" in values


def test_named_token_metadata(cfg: Config) -> None:
    secrets.add_github_token(
        cfg, "work", "ghp_work", meta={"login": "octocat", "token_type": "classic"}
    )
    meta = secrets.token_meta(cfg, "work")
    assert meta is not None
    assert meta["login"] == "octocat"
    assert meta["token_type"] == "classic"
    entry = secrets.list_github_tokens(cfg)[0]
    assert entry["token"] == "ghp_work"
    assert entry["login"] == "octocat"


def test_add_github_token_rejects_empty_name(cfg: Config) -> None:
    with pytest.raises(ValueError, match="empty"):
        secrets.add_github_token(cfg, " ", "ghp_x")


def test_update_github_token_replaces_token_and_meta(cfg: Config) -> None:
    secrets.add_github_token(cfg, "work", "ghp_old", meta={"login": "old-user"})
    secrets.add_github_token(cfg, "personal", "ghp_personal")
    secrets.update_github_token(
        cfg, "work", "ghp_new", meta={"login": "new-user", "token_type": "fine-grained"}
    )
    assert set(secrets.token_names(cfg)) == {"work", "personal"}
    assert secrets.get_named_token(cfg, "work") == "ghp_new"
    # Unrelated accounts are untouched.
    assert secrets.get_named_token(cfg, "personal") == "ghp_personal"
    meta = secrets.token_meta(cfg, "work")
    assert meta is not None
    assert meta["login"] == "new-user"
    assert meta["token_type"] == "fine-grained"


def test_update_github_token_unknown_name_raises(cfg: Config) -> None:
    secrets.add_github_token(cfg, "work", "ghp_work")
    with pytest.raises(KeyError, match="no such"):
        secrets.update_github_token(cfg, "missing", "ghp_x")
    # The existing account is untouched by the failed update.
    assert secrets.get_named_token(cfg, "work") == "ghp_work"
