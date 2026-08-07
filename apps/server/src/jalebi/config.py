"""Environment and data-directory configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT: Path | None = None


def repo_root() -> Path:
    """Locate the repo root by walking up from this file to the nearest git/package root."""
    global _REPO_ROOT
    if _REPO_ROOT is None:
        current = Path(__file__).resolve().parent
        for parent in current.parents:
            if (parent / "package.json").is_file() and (parent / ".git").is_dir():
                _REPO_ROOT = parent
                break
        else:
            _REPO_ROOT = current
    return _REPO_ROOT


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 3456
    data_dir: Path = field(default_factory=lambda: Path.home() / ".jalebi")

    @property
    def db_url(self) -> str:
        """SQLAlchemy URL for the SQLite DB.

        The sqlite dialect passes ``url.database`` straight to sqlite3 (no
        percent-decoding), so a raw filesystem path is correct — spaces work.
        ``?``/``#`` would be parsed as query/fragment and silently truncate the
        path, so refuse them with a clear error instead.
        """
        path = str(self.data_dir / "data.db")
        for ch in ("?", "#"):
            if ch in path:
                raise ValueError(
                    f"JALEBI_DATA_DIR contains {ch!r}, which breaks a sqlite URL: {self.data_dir}"
                )
        return f"sqlite:///{path}"

    def ensure_dirs(self) -> None:
        """Create the data directory and its subdirectories (PRD F12)."""
        for sub in ("", "repos", "ws", "agents", "logs"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    """Load .env from the repo root (without overriding existing env vars) and build a Config."""
    load_dotenv(repo_root() / ".env")
    try:
        port = int(os.environ.get("JALEBI_PORT", "3456"))
    except ValueError:
        port = 3456  # junk JALEBI_PORT should not crash startup
    return Config(
        host=os.environ.get("JALEBI_HOST", "127.0.0.1"),
        port=port,
        data_dir=Path(os.environ.get("JALEBI_DATA_DIR") or Path.home() / ".jalebi"),
    )
