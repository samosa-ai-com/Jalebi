"""Phase 4 T6 — IDE connector.

Settings + validation + safe spawn for "open this task's worktree in
my IDE". The settings validator is the **security boundary** — nothing
user-controlled reaches the command line.

- ``validate_ide_command(value)`` is the single gate: non-str → False;
  empty string → True (feature off); must match ``^[A-Za-z0-9._/=:-]+$``
  (no whitespace, no shell metachars). If absolute → must ``isfile``;
  else must ``shutil.which``.
- ``open_in_ide(command, path)`` resolves the command and spawns
  ``[resolved, str(path)]`` via ``subprocess.Popen`` with
  ``start_new_session=True`` + ``DEVNULL`` + ``close_fds=True`` and
  **no shell**. Anything that slips past the validator would have to
  also resolve on PATH or be an existing file — the rendered argv is
  the only surface an attacker can influence.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from jalebi import settings

# Whitelist of binaries the Settings UI's "Detect" button probes (in order).
# Short list — no PATH scanning, just one shutil.which per entry.
IDE_CANDIDATES = [
    "code",
    "cursor",
    "codium",
    "nvim",
    "vim",
    "subl",
    "idea",
    "webstorm",
    "pycharm",
    "phpstorm",
    "goland",
]

# Display name for each known binary.
IDE_DISPLAY_NAMES = {
    "code": "VS Code",
    "cursor": "Cursor",
    "codium": "VSCodium",
    "nvim": "Neovim",
    "vim": "Vim",
    "subl": "Sublime Text",
    "idea": "IntelliJ IDEA",
    "webstorm": "WebStorm",
    "pycharm": "PyCharm",
    "phpstorm": "PhpStorm",
    "goland": "GoLand",
}

# Security: only this character class is allowed in a stored command. No
# whitespace, no shell metacharacters, no quoting — anything that would
# let a stray `<>|&;\"'` etc. survive into argv.
_IDE_COMMAND_RE = re.compile(r"^[A-Za-z0-9._/=:-]+$")


class IdeError(Exception):
    """Raised by ``open_in_ide`` when the configured command can't be used."""


def validate_ide_command(value: object) -> bool:
    """True iff ``value`` is a safe, resolvable IDE command.

    ``""`` is valid (feature off). Anything else must match the regex and
    resolve via ``shutil.which`` (bare name) or ``isfile`` (absolute path).
    """
    if not isinstance(value, str):
        return False
    if value == "":
        return True
    if not _IDE_COMMAND_RE.match(value):
        return False
    if value.startswith("/"):
        return os.path.isfile(value)
    return shutil.which(value) is not None


def resolve_command(command: str) -> str | None:
    """Return the resolved path of ``command`` (or ``None`` if unusable)."""
    if not command or not _IDE_COMMAND_RE.match(command):
        return None
    if command.startswith("/"):
        return command if os.path.isfile(command) else None
    return shutil.which(command)


def detect_ide() -> tuple[str, str] | None:
    """First IDE_CANDIDATES entry on PATH → (command, display_name)."""
    for name in IDE_CANDIDATES:
        if shutil.which(name):
            return name, IDE_DISPLAY_NAMES.get(name, name)
    return None


def ide_status(session) -> dict:
    """``GET /api/ide/status`` payload — what's currently configured + usable."""
    command = str(settings.get_setting(session, "ide_command") or "")
    name = str(settings.get_setting(session, "ide_name") or "")
    return {
        "command": command,
        "name": name,
        "found": resolve_command(command) is not None,
    }


def test_open(session) -> tuple[bool, str]:
    """Open on a scratch dir to verify the configured IDE actually launches."""
    command = str(settings.get_setting(session, "ide_command") or "")
    resolved = resolve_command(command)
    if resolved is None:
        return False, "no IDE configured (or command not found)"
    scratch = Path(tempfile.mkdtemp(prefix="jalebi-ide-test-"))
    try:
        subprocess.Popen(  # noqa: S603 — argv list, no shell
            [resolved, str(scratch)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        return False, f"failed to launch: {exc}"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return True, ""


def open_in_ide(command: str, path: Path) -> None:
    """Spawn the configured IDE on the given worktree path.

    Raises ``IdeError`` when the command isn't configured, doesn't
    resolve, or fails to launch. The caller is the route handler — it
    converts the error to a 5xx response.
    """
    resolved = resolve_command(command)
    if resolved is None:
        raise IdeError("IDE not configured or command not found")
    try:
        subprocess.Popen(  # noqa: S603 — argv list, no shell
            [resolved, str(path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        raise IdeError(f"failed to launch IDE: {exc}") from exc
