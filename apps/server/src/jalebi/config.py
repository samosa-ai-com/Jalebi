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
        return f"sqlite:///{self.data_dir.as_posix()}/data.db"

    def ensure_dirs(self) -> None:
        """Create the data directory and its subdirectories (PRD F12)."""
        for sub in ("", "repos", "ws", "agents", "logs"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    """Load .env from the repo root (without overriding existing env vars) and build a Config."""
    load_dotenv(repo_root() / ".env")
    return Config(
        host=os.environ.get("JALEBI_HOST", "127.0.0.1"),
        port=int(os.environ.get("JALEBI_PORT", "3456")),
        data_dir=Path(os.environ.get("JALEBI_DATA_DIR") or Path.home() / ".jalebi"),
    )
