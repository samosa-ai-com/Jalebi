"""Encrypted-on-disk secrets store (``secrets.json`` with 0600 permissions)."""

import json
import os
from pathlib import Path

from jalebi.config import Config

SECRETS_FILE = "secrets.json"
GITHUB_TOKEN_KEY = "github_token"
GITHUB_TOKENS_KEY = "github_tokens"
ENV_GITHUB_TOKEN = "JALEBI_GITHUB_TOKEN"


def secrets_path(config: Config) -> Path:
    return config.data_dir / SECRETS_FILE


def _load(config: Config) -> dict:
    path = secrets_path(config)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _store(config: Config, data: dict) -> None:
    path = secrets_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def store_secret(config: Config, key: str, value: str) -> None:
    """Persist ``value`` under ``key`` in the 0600 secrets file."""
    data = _load(config)
    data[key] = value
    _store(config, data)


def load_secret(config: Config, key: str) -> str | None:
    data = _load(config)
    return data.get(key)


def list_github_tokens(config: Config) -> list[dict[str, str]]:
    """Return the named PATs as ``[{"name": ..., "token": ...}, ...]``."""
    data = _load(config)
    raw = data.get(GITHUB_TOKENS_KEY) or []
    return [
        {"name": str(item.get("name")), "token": str(item.get("token"))}
        for item in raw
        if isinstance(item, dict) and item.get("name") and item.get("token")
    ]


def add_github_token(config: Config, name: str, token: str) -> None:
    """Add (or replace, by name) a named PAT."""
    data = _load(config)
    tokens = data.get(GITHUB_TOKENS_KEY) or []
    if not isinstance(tokens, list):
        tokens = []
    tokens = [t for t in tokens if not (isinstance(t, dict) and t.get("name") == name)]
    tokens.append({"name": name, "token": token})
    data[GITHUB_TOKENS_KEY] = tokens
    _store(config, data)


def remove_github_token(config: Config, name: str) -> None:
    data = _load(config)
    tokens = data.get(GITHUB_TOKENS_KEY) or []
    data[GITHUB_TOKENS_KEY] = [
        t for t in tokens if not (isinstance(t, dict) and t.get("name") == name)
    ]
    _store(config, data)


def token_names(config: Config) -> list[str]:
    return [t["name"] for t in list_github_tokens(config)]


def get_named_token(config: Config, name: str) -> str | None:
    for item in list_github_tokens(config):
        if item["name"] == name:
            return item["token"]
    return None


def load_github_token(config: Config) -> str | None:
    """Return the primary GitHub PAT: env wins, then stored default, then first named."""
    env_token = os.environ.get(ENV_GITHUB_TOKEN)
    if env_token:
        return env_token
    stored = load_secret(config, GITHUB_TOKEN_KEY)
    if stored:
        return stored
    tokens = list_github_tokens(config)
    return tokens[0]["token"] if tokens else None


def resolve_token(config: Config, name: str | None) -> str | None:
    """Resolve a named PAT, falling back to the primary token."""
    if name:
        token = get_named_token(config, name)
        if token:
            return token
    return load_github_token(config)


def all_token_values(config: Config) -> list[str]:
    """Every known PAT value (for masking)."""
    values: list[str] = []
    env_token = os.environ.get(ENV_GITHUB_TOKEN)
    if env_token:
        values.append(env_token)
    stored = load_secret(config, GITHUB_TOKEN_KEY)
    if stored:
        values.append(stored)
    values.extend(t["token"] for t in list_github_tokens(config))
    return list(dict.fromkeys(values))


def persist_env_github_token(config: Config) -> None:
    """If ``JALEBI_GITHUB_TOKEN`` is set, mirror it into the secrets file (PRD F1)."""
    env_token = os.environ.get(ENV_GITHUB_TOKEN)
    if env_token and env_token != load_secret(config, GITHUB_TOKEN_KEY):
        store_secret(config, GITHUB_TOKEN_KEY, env_token)
