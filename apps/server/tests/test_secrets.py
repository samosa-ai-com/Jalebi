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


def test_env_token_wins_over_stored(cfg: Config, monkeypatch) -> None:
    secrets.store_secret(cfg, "github_token", "stored")
    monkeypatch.setenv(secrets.ENV_GITHUB_TOKEN, "env_token")
    assert secrets.load_github_token(cfg) == "env_token"


def test_stored_token_used_when_no_env(cfg: Config, monkeypatch) -> None:
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.store_secret(cfg, "github_token", "stored")
    assert secrets.load_github_token(cfg) == "stored"


def test_persist_env_github_token(cfg: Config, monkeypatch) -> None:
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    assert secrets.load_secret(cfg, "github_token") is None
    monkeypatch.setenv(secrets.ENV_GITHUB_TOKEN, "ghp_abc")
    secrets.persist_env_github_token(cfg)
    assert secrets.load_secret(cfg, "github_token") == "ghp_abc"
    secrets.persist_env_github_token(cfg)  # idempotent
    assert secrets.load_secret(cfg, "github_token") == "ghp_abc"
