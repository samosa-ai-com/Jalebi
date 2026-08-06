"""Encrypted-on-disk secrets store (``secrets.json`` with 0600 permissions)."""

import json
import os
from pathlib import Path

from jalebi.config import Config

SECRETS_FILE = "secrets.json"
GITHUB_TOKEN_KEY = "github_token"
ENV_GITHUB_TOKEN = "JALEBI_GITHUB_TOKEN"


def secrets_path(config: Config) -> Path:
    return config.data_dir / SECRETS_FILE


def store_secret(config: Config, key: str, value: str) -> None:
    """Persist ``value`` under ``key`` in the 0600 secrets file."""
    path = secrets_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        data = json.loads(path.read_text())
    data[key] = value
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def load_secret(config: Config, key: str) -> str | None:
    path = secrets_path(config)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data.get(key)


def load_github_token(config: Config) -> str | None:
    """Return the GitHub PAT: env var wins, else the stored secrets file."""
    env_token = os.environ.get(ENV_GITHUB_TOKEN)
    if env_token:
        return env_token
    return load_secret(config, GITHUB_TOKEN_KEY)


def persist_env_github_token(config: Config) -> None:
    """If ``JALEBI_GITHUB_TOKEN`` is set, mirror it into the secrets file (PRD F1)."""
    env_token = os.environ.get(ENV_GITHUB_TOKEN)
    if env_token and env_token != load_secret(config, GITHUB_TOKEN_KEY):
        store_secret(config, GITHUB_TOKEN_KEY, env_token)
