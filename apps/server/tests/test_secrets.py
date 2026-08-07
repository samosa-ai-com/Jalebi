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


def test_stored_token_wins_over_env(cfg: Config, monkeypatch) -> None:
    secrets.store_secret(cfg, "github_token", "stored")
    monkeypatch.setenv(secrets.ENV_GITHUB_TOKEN, "env_token")
    assert secrets.load_github_token(cfg) == "stored"


def test_env_token_used_when_no_stored(cfg: Config, monkeypatch) -> None:
    monkeypatch.setenv(secrets.ENV_GITHUB_TOKEN, "env_token")
    assert secrets.load_github_token(cfg) == "env_token"


def test_stored_token_used_when_no_env(cfg: Config, monkeypatch) -> None:
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.store_secret(cfg, "github_token", "stored")
    assert secrets.load_github_token(cfg) == "stored"


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


def test_resolve_named_then_primary(cfg: Config, monkeypatch) -> None:
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.add_github_token(cfg, "work", "ghp_work")
    assert secrets.resolve_token(cfg, "work") == "ghp_work"
    # unknown name falls back to the primary (here: the first named token)
    assert secrets.resolve_token(cfg, "missing") == "ghp_work"
    secrets.store_secret(cfg, "github_token", "ghp_primary")
    assert secrets.resolve_token(cfg, "missing") == "ghp_primary"
    assert secrets.resolve_token(cfg, None) == "ghp_primary"


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


def test_add_github_token_reserves_default_name(cfg: Config) -> None:
    with pytest.raises(ValueError, match="reserved"):
        secrets.add_github_token(cfg, "default", "ghp_x")
