"""Phase 4 T7 — read-only in-worktree file browser/viewer.

Serves a task's worktree files for inline viewing (markdown/code/JSON).
Read-only; editing in the worktree is out of scope. Mirrors the path
safety of ``artifacts.artifact_file``: the worktree root is the boundary,
and we refuse traversal, ``.git`` segments, and symlinks escaping the root.

Content is size-capped and NUL-sniffed (binary → caller returns 415).
Masking of the returned text is the route's job (it has the task's
masker), consistent with how the diff endpoints mask.
"""

import os
from pathlib import Path

from jalebi.git_workspace import GitWorkspace

MAX_VIEW_BYTES = 256 * 1024
_BINARY_SNIFF = 8 * 1024


def _resolve(config, task_id: int, rel_path: str) -> tuple[Path, str | None]:
    """Resolve ``rel_path`` under the task's worktree root; return (target, error)."""
    root = GitWorkspace.worktree_path(config.data_dir, task_id)
    if not (root / ".git").is_file():
        return root, "task has no worktree yet"
    # Reject absolute / non-str client paths.
    if not isinstance(rel_path, str) or Path(rel_path).is_absolute():
        return root, "invalid path"
    raw = root / rel_path
    # Refuse symlinks anywhere along the client-supplied path — checking
    # only the final component misses an intermediate symlink such as
    # ``link/config`` where ``link -> .git`` (or -> /outside). Walk each
    # prefix of the raw path before resolving.
    cur = root
    for part in Path(rel_path).parts:
        if part in ("", "."):
            continue
        cur = cur / part
        try:
            if cur.is_symlink():
                return root, "refusing symlink"
        except OSError:
            return root, "invalid path"
    # Legacy direct check: the final component itself is a symlink.
    if raw.is_symlink():
        return root, "refusing symlink"
    target = raw.resolve()
    # Containment on the resolved path (the true security boundary).
    root_real = root.resolve()
    if not target.is_relative_to(root_real):
        return root, "path escapes worktree"
    # .git segments (case-insensitive: `.Git`/`.GIT` resolve to the same
    # directory on case-insensitive mounts) on the RESOLVED relative path
    # so an intermediate symlink into .git is caught even if the raw parts
    # name no .git segment. Keep the raw-parts check too so `foo/../.git`
    # can't slip through on resolution quirks.
    resolved_parts = target.relative_to(root_real).parts
    if (
        any(p.lower() == ".git" for p in resolved_parts)
        or any(p.lower() == ".git" for p in raw.parts)
    ):
        return root, "path is inside .git"
    return target, None


def list_worktree_dir(config, task_id: int, rel_path: str) -> tuple[list[dict], str | None]:
    """List a directory under the worktree. Returns (entries, error)."""
    target, error = _resolve(config, task_id, rel_path)
    if error is not None:
        return [], error
    if not target.is_dir():
        return [], "not a directory"

    root = GitWorkspace.worktree_path(config.data_dir, task_id)
    entries: list[dict] = []
    try:
        with os.scandir(target) as it:
            for e in it:
                if e.name == ".git":
                    continue
                # Never surface symlinked entries.
                if e.is_symlink():
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                rel = Path(e.path).relative_to(root).as_posix()
                entries.append(
                    {
                        "name": e.name,
                        "path": rel,
                        "is_dir": is_dir,
                        "size": e.stat().st_size if not is_dir else 0,
                        "extension": _ext(e.name) if not is_dir else "",
                    }
                )
    except OSError as exc:
        return [], f"failed to list directory: {exc}"

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return entries, None


def read_worktree_file(config, task_id: int, rel_path: str) -> tuple[str | None, str | None]:
    """Read a text file under the worktree. Returns (content, error).

    ``error`` is ``"binary"`` for binary files (caller maps to 415),
    otherwise a message. Content is NOT masked here — the route masks it.
    """
    target, error = _resolve(config, task_id, rel_path)
    if error is not None:
        return None, error
    if not target.is_file():
        return None, "not a file"
    try:
        if target.stat().st_size > MAX_VIEW_BYTES:
            return None, "file too large for inline view"
        data = target.read_bytes()
    except OSError as exc:
        return None, f"failed to read file: {exc}"
    if b"\x00" in data[:_BINARY_SNIFF]:
        return None, "binary"
    return data.decode("utf-8", errors="replace"), None


def _ext(name: str) -> str:
    i = name.rfind(".")
    return name[i + 1:].lower() if i >= 0 else ""
