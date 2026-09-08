"""Environment and data-directory configuration."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _parse_base(candidate: str) -> tuple[str, str] | None:
    """Validate a URL base → ``(hostname, base)`` or ``None`` when unusable.

    Uses ``urlsplit`` so schemeless typos (``http://``), path-only junk
    (``/``), IPv6 hosts (``http://[::1]:2052`` → label ``::1``), and
    ``user:pass@host`` forms are all handled without string surgery.
    """
    candidate = candidate.strip()
    if not candidate:
        return None
    base = candidate if "://" in candidate else "http://" + candidate
    base = base.rstrip("/")
    try:
        host = urlsplit(base).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host, base


def parse_public_urls(raw: str) -> list[tuple[str, str]]:
    """Parse ``JALEBI_PUBLIC_URLS`` into an ordered ``[(label, base_url)]`` list.

    Format: comma-separated ``Label=url`` entries (``LAN=http://192.0.2.1:2052``);
    a bare URL without a label gets its hostname as the label. Anything before
    the first ``=`` counts as a label unless it looks like a URL itself (contains
    ``://`` or ``/``) — so labels may contain dots/parens (``Laptop.local=…``)
    while bare URLs with query strings (``http://host/?a=b``) are never split.
    Junk entries are skipped (logged) so a typo can never break startup or
    notifications.
    """
    links: list[tuple[str, str]] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        label, candidate = "", item
        head, sep, tail = item.partition("=")
        if sep and not head.strip():
            logger.warning("ignoring malformed public URL in JALEBI_PUBLIC_URLS: %r", item)
            continue
        if sep and "://" not in head and "/" not in head:
            label, candidate = head.strip(), tail.strip()
        parsed = _parse_base(candidate)
        if parsed is None:
            logger.warning("ignoring malformed public URL in JALEBI_PUBLIC_URLS: %r", item)
            continue
        host, base = parsed
        links.append((label or host, base))
    return links

_REPO_ROOT: Path | None = None


def _is_repo_root(path: Path) -> bool:
    """A repo root: has ``package.json`` and a ``.git`` entry. ``.git`` may be a
    directory (normal clone) or a file (a linked git worktree)."""
    return (path / "package.json").is_file() and (path / ".git").exists()


def repo_root() -> Path:
    """Locate the repo root by walking up from this file to the nearest git/package root."""
    global _REPO_ROOT
    if _REPO_ROOT is None:
        current = Path(__file__).resolve().parent
        for parent in current.parents:
            if _is_repo_root(parent):
                _REPO_ROOT = parent
                break
        else:
            _REPO_ROOT = current
    return _REPO_ROOT


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"
    port: int = 2052
    data_dir: Path = field(default_factory=lambda: Path.home() / ".jalebi")
    password: str = ""
    public_urls: str = ""

    def public_links(self) -> list[tuple[str, str]]:
        """Ordered ``[(label, base_url)]`` from ``JALEBI_PUBLIC_URLS`` ([] when unset)."""
        return parse_public_urls(self.public_urls)

    def primary_link(self, path: str) -> str:
        """Tap-to-open URL: first configured public URL, else loopback."""
        links = self.public_links()
        base = links[0][1] if links else f"http://127.0.0.1:{self.port}"
        return base + path

    def open_actions(self, path: str, single_label: str) -> list[dict[str, object]]:
        """ntfy ``view`` buttons for a notification path.

        Zero/one configured URL → exactly one button with the caller's legacy
        label (payloads unchanged from before). Multiple URLs → one button per
        URL (``Open (<label>)``), capped at 3 — the most ntfy renders.
        """
        links = self.public_links()
        if len(links) <= 1:
            base = links[0][1] if links else f"http://127.0.0.1:{self.port}"
            return [{"action": "view", "label": single_label, "url": base + path}]
        return [
            {"action": "view", "label": f"Open ({label})", "url": base + path}
            for label, base in links[:3]
        ]

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
        port = int(os.environ.get("JALEBI_PORT", "2052"))
    except ValueError:
        port = 2052  # junk JALEBI_PORT should not crash startup
    return Config(
        host=os.environ.get("JALEBI_HOST", "0.0.0.0"),
        port=port,
        data_dir=Path(os.environ.get("JALEBI_DATA_DIR") or Path.home() / ".jalebi"),
        # Mandatory UI password (PRD §F13): gates the API + SPA behind Basic auth.
        # The server binds to 0.0.0.0 by default for LAN access, so a password
        # is required at startup — main() refuses to start when this is empty.
        password=os.environ.get("JALEBI_PASSWORD")
        or os.environ.get("OPENCODE_SERVER_PASSWORD")
        or "",
        # Optional LAN/Tailscale/tunnel URLs for notification tap-links
        # (comma-separated `Label=url`; first is primary). Unset → loopback.
        public_urls=os.environ.get("JALEBI_PUBLIC_URLS") or "",
    )
