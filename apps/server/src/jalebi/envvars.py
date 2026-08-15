"""Env-var store service: CRUD over the ``env_vars`` table (masked at the API)."""

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi import clock
from jalebi.db import EnvVar, Repo, now


def list_env_vars(session: Session, repo_id: int | None = None) -> list[EnvVar]:
    """All env vars, optionally filtered to one repo (plus globals)."""
    q = select(EnvVar)
    if repo_id is not None:
        q = q.where((EnvVar.repo_id.is_(None)) | (EnvVar.repo_id == repo_id))
    return list(session.execute(q.order_by(EnvVar.name)).scalars())


def get_env_var(session: Session, env_var_id: int) -> EnvVar | None:
    return session.get(EnvVar, env_var_id)


def upsert_env_var(
    session: Session,
    *,
    name: str,
    value: str,
    repo_id: int | None = None,
) -> EnvVar:
    """Add or update an env var (unique per name+repo scope)."""
    name = name.strip()
    if not name or "=" in name or " " in name:
        raise ValueError(f"invalid env var name: {name!r}")
    if repo_id is not None and session.get(Repo, repo_id) is None:
        raise ValueError(f"repo {repo_id} not found")
    row = session.execute(
        select(EnvVar).where(EnvVar.name == name, EnvVar.repo_id == repo_id)
    ).scalar_one_or_none()
    if row is None:
        row = EnvVar(name=name, value=value, repo_id=repo_id)
        session.add(row)
    else:
        row.value = value
        row.updated_at = now()
    session.commit()
    session.refresh(row)
    return row


def delete_env_var(session: Session, env_var_id: int) -> bool:
    row = session.get(EnvVar, env_var_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def import_env_file(
    session: Session, content: str, repo_id: int | None = None
) -> tuple[int, list[str]]:
    """Parse ``KEY=VALUE`` lines (a .env file) and upsert each.

    Returns ``(imported_count, skipped_lines)`` — lines with an invalid key
    (empty, or containing ``=``/whitespace) or no ``=`` are reported so the
    caller can tell the user what was ignored.
    """
    imported = 0
    skipped: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key or re.search(r"[=\s]", key):
            skipped.append(line.strip())
            continue
        value = value.strip().strip("'\"")
        upsert_env_var(session, name=key, value=value, repo_id=repo_id)
        imported += 1
    return imported, skipped


def task_env_names(task) -> list[str]:
    """The env-var names a task selected (from its ``env_vars_json``)."""
    try:
        names = json.loads(task.env_vars_json) if task.env_vars_json else []
    except (ValueError, TypeError):
        names = []
    return [str(n) for n in names] if isinstance(names, list) else []


def values_for_names(session: Session, repo_id: int, names: list[str]) -> dict[str, str]:
    """Resolve selected names to values, preferring repo-scoped over global.

    A task inherits global vars plus the repo's own; a name defined at both
    scopes resolves to the repo-scoped value.
    """
    if not names:
        return {}
    wanted = set(names)
    result: dict[str, str] = {}
    # Repo-scoped values win; globals fill in anything the repo doesn't override.
    rows = list_env_vars(session, repo_id=repo_id)
    repo_rows = [r for r in rows if r.repo_id is not None]
    global_rows = [r for r in rows if r.repo_id is None]
    for row in repo_rows + global_rows:
        if row.name in wanted and row.name not in result:
            result[row.name] = row.value
    return result


def env_var_to_dict(row: EnvVar, repo_full_name: str | None = None) -> dict[str, object]:
    """API shape — the value is NEVER returned in full, only a masked preview."""
    value = row.value
    if not value:
        masked = "***"
    elif len(value) > 8:
        masked = value[:4] + "***" + value[-2:]
    else:
        masked = "***"
    return {
        "id": row.id,
        "name": row.name,
        "masked": masked,
        "repo_id": row.repo_id,
        "repo_full_name": repo_full_name,
        "created_at": clock.to_iso(row.created_at),
    }
