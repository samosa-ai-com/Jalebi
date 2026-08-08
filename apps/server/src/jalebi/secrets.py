"""Encrypted-on-disk secrets store (``secrets.json`` with 0600 permissions).

All PATs are **equal named accounts**. There is no primary/default token and no
fallback: ``resolve_token(config, name)`` returns the token of the named account
``name`` or ``None`` — never a different account. ``JALEBI_GITHUB_TOKEN`` and a
legacy stored ``github_token`` are retained only as *masking* inputs (so a
stray value never survives into logs) and are never used for resolution.
"""

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
    """Return the named PATs as ``[{"name": ..., "token": ..., ...}]``."""
    data = _load(config)
    raw = data.get(GITHUB_TOKENS_KEY) or []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name") or not item.get("token"):
            continue
        entry = {
            "name": str(item["name"]),
            "token": str(item["token"]),
        }
        for key in ("login", "token_type", "granted_scopes", "missing_scopes", "note"):
            if key in item:
                entry[key] = item[key]
        out.append(entry)
    return out


def add_github_token(
    config: Config, name: str, token: str, meta: dict | None = None
) -> None:
    """Add (or replace, by name) a named PAT account.

    Every PAT is an equal named account — there is no reserved/default name.
    """
    if not name or not name.strip():
        raise ValueError("account name must not be empty")
    data = _load(config)
    tokens = data.get(GITHUB_TOKENS_KEY) or []
    if not isinstance(tokens, list):
        tokens = []
    tokens = [t for t in tokens if not (isinstance(t, dict) and t.get("name") == name)]
    entry: dict = {"name": name, "token": token}
    if meta:
        entry.update(meta)
    tokens.append(entry)
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


def token_meta(config: Config, name: str) -> dict[str, str] | None:
    for item in list_github_tokens(config):
        if item["name"] == name:
            return item
    return None


def get_named_token(config: Config, name: str) -> str | None:
    for item in list_github_tokens(config):
        if item["name"] == name:
            return item["token"]
    return None


def resolve_token(config: Config, name: str | None) -> str | None:
    """Return the token for the named account, or ``None``.

    Strict: there is NO fallback to any "primary"/"default"/first vault entry.
    ``None`` or an unknown name resolves to ``None`` — the caller surfaces it as
    an explicit error, never as a silently-picked different account.
    """
    if not name:
        return None
    return get_named_token(config, name)


def all_token_values(config: Config) -> list[str]:
    """Every known PAT value (for masking only — never for resolution).

    Includes the env/bootstrap token and any legacy stored ``github_token`` so a
    stray value still gets redacted from logs, plus every named account.
    """
    values: list[str] = []
    env_token = os.environ.get(ENV_GITHUB_TOKEN)
    if env_token:
        values.append(env_token)
    stored = load_secret(config, GITHUB_TOKEN_KEY)
    if stored:
        values.append(stored)
    values.extend(t["token"] for t in list_github_tokens(config))
    return list(dict.fromkeys(values))
