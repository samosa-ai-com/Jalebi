"""App-data insight + maintenance (Settings → Data management).

Read model: everything under the configured ``data_dir`` —
``data.db`` (all tables), ``repos/`` (bare mirrors), ``ws/`` (per-task
worktrees), ``artifacts/<run_id>/``, ``logs/``, ``backups/`` — plus the
0600 ``secrets.json`` vault (never read, only sized).

Write model: destructive actions always run as **preview first**
(``prune_preview`` returns counts, the UI shows them) and only execute on
explicit confirm. Prune never touches queued/running/blocked tasks; backup
uses the SQLite backup API (a raw file copy of a live WAL DB can be corrupt).
"""

import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from jalebi import artifacts, tasks
from jalebi.db import Artifact, EventDelivery, Repo, Run, ScreeningRun, Task, TaskEvent, now
from jalebi.git_workspace import GitWorkspace

BACKUP_DIRNAME = "backups"
_BACKUP_RE = re.compile(r"^data-\d{8}-\d{6}(?:-\d+)?\.db$")

# Only these task statuses may be pruned. queued/running stay alive;
# blocked/waiting_review/needs_approval still need the owner.
PRUNABLE_STATUSES = ("done", "failed", "timed_out", "cancelled", "interrupted")

VALID_PRUNE_SCOPES = ("tasks", "orphans", "deliveries", "logs")

# SQLite caps bound variables per statement (default 999): never pass an
# unbounded Python id list into a single ``IN`` clause — chunk instead so
# pruning thousands of rows can't 500 on the variable limit.
_IN_CHUNK = 500


def _chunked(ids: list[int], size: int = _IN_CHUNK):
    """Yield successive slices of ``ids`` (empty input yields nothing)."""
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


def _dir_size(path: Path) -> int:
    """Recursive byte size of ``path`` (0 when missing; unreadable files skipped)."""
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def usage(session: Session, data_dir: Path) -> dict:
    """Storage + row counts for the Data management dashboard."""
    data_dir = Path(data_dir)
    counts = {
        "tasks": session.execute(select(func.count()).select_from(Task)).scalar() or 0,
        "runs": session.execute(select(func.count()).select_from(Run)).scalar() or 0,
        "task_events": session.execute(select(func.count()).select_from(TaskEvent)).scalar()
        or 0,
        "artifacts": session.execute(select(func.count()).select_from(Artifact)).scalar()
        or 0,
        "deliveries": session.execute(select(func.count()).select_from(EventDelivery)).scalar()
        or 0,
    }
    by_status: dict[str, int] = {}
    for row in session.execute(
        select(Task.status, func.count()).group_by(Task.status)
    ).all():
        by_status[str(row[0])] = int(row[1])
    sizes = {
        # WAL mode: the live DB spans data.db + -wal + -shm — size them all.
        "db": sum(
            _dir_size(data_dir / f"data.db{suffix}") for suffix in ("", "-wal", "-shm")
        ),
        "mirrors": _dir_size(data_dir / "repos"),
        "worktrees": _dir_size(data_dir / "ws"),
        "artifacts": _dir_size(artifacts.artifact_store_dir(data_dir)),
        "logs": _dir_size(data_dir / "logs"),
        "backups": _dir_size(data_dir / BACKUP_DIRNAME),
        "secrets": _dir_size(data_dir / "secrets.json"),
    }
    return {"sizes": sizes, "counts": counts, "tasks_by_status": by_status}


# -- backups ---------------------------------------------------------------


def backups_dir(data_dir: Path) -> Path:
    return Path(data_dir) / BACKUP_DIRNAME


def list_backups(data_dir: Path) -> list[dict]:
    """Timestamped backups, newest first (name/size/created_at)."""
    out = []
    try:
        entries = sorted(backups_dir(data_dir).glob("data-*.db"), reverse=True)
    except OSError:
        return []
    for path in entries:
        if not _BACKUP_RE.match(path.name):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return out


def create_backup(data_dir: Path) -> dict:
    """Consistent snapshot of the live DB via the SQLite backup API.

    The snapshot is chmod'd ``0600`` like the secrets vault — the DB holds
    plaintext env-var values. Same-second collisions get a numeric suffix.
    """
    data_dir = Path(data_dir)
    src_path = data_dir / "data.db"
    if not src_path.is_file():
        raise FileNotFoundError("no database to back up yet")
    dest_dir = backups_dir(data_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime("%Y%m%d-%H%M%S")
    name = f"data-{stamp}.db"
    suffix = 2
    while (dest_dir / name).exists():
        name = f"data-{stamp}-{suffix}.db"
        suffix += 1
    dest = dest_dir / name
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(str(dest), timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(dest, 0o600)
    stat = dest.stat()
    return {
        "name": name,
        "size": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def backup_file(data_dir: Path, name: str) -> Path:
    """Resolve a backup for download, refusing traversal/foreign names."""
    if not _BACKUP_RE.match(name):
        raise ValueError("unknown backup")
    candidate = (backups_dir(Path(data_dir)) / name).resolve()
    if candidate.parent != backups_dir(Path(data_dir)).resolve() or not candidate.is_file():
        raise ValueError("unknown backup")
    return candidate


def delete_backup(data_dir: Path, name: str) -> bool:
    """Delete one backup; False when it doesn't exist."""
    try:
        path = backup_file(data_dir, name)
    except ValueError:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


class RestoreError(Exception):
    """Restoration failed (the live DB is untouched or rolled back)."""


class RestoreBusy(Exception):
    """Tasks or screening runs are active — restore refused to protect live work."""


def backup_integrity(data_dir: Path, name: str) -> tuple[bool, str | None]:
    """Read-only ``PRAGMA integrity_check`` on a backup. Never writes."""
    try:
        path = backup_file(data_dir, name)
    except ValueError:
        return False, "unknown backup"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except Exception as exc:
        return False, f"unreadable backup: {exc}"
    if rows == [("ok",)]:
        return True, None
    return False, "; ".join(str(r[0]) for r in rows[:3])


def busy_task_count(session: Session) -> int:
    """Tasks that would be endangered by a restore (queued/running)."""
    return (
        session.execute(
            select(func.count())
            .select_from(Task)
            .where(Task.status.in_(("queued", "running")))
        ).scalar()
        or 0
    )


def busy_screening_count(session: Session) -> int:
    """Screening runs that would be endangered by a restore (queued/running).

    Screening runs live in ``ScreeningRun``, not ``Task`` — without this gate
    a restore could swap the DB out from under the scheduler/engine mid-audit.
    """
    return (
        session.execute(
            select(func.count())
            .select_from(ScreeningRun)
            .where(ScreeningRun.status.in_(("queued", "running")))
        ).scalar()
        or 0
    )


def restore_backup(config, session: Session, name: str, queue=None) -> dict:
    """Restore the live DB from a backup (execute path — preview first).

    Order protects the current database: idle gate → read-only integrity
    check → safety snapshot (new file only) → engine dispose → swap →
    re-init/migrate/seed/resync. Anything failing before the swap leaves the
    live DB untouched; a swap failure rolls back from the safety snapshot.
    """
    from jalebi import clock, db
    from jalebi import settings as settings_mod

    data_dir = Path(config.data_dir)
    try:
        source = backup_file(data_dir, name)
    except ValueError:
        raise RestoreError("unknown backup")
    if busy_task_count(session) > 0:
        raise RestoreBusy("tasks are queued or running — finish or cancel them first")
    if busy_screening_count(session) > 0:
        raise RestoreBusy("screening runs are queued or running — wait for them first")
    ok, detail = backup_integrity(data_dir, name)
    if not ok:
        raise RestoreError(detail or "backup failed integrity check")

    # Safety snapshot BEFORE touching anything live.
    safety = create_backup(data_dir)
    live = data_dir / "data.db"

    session.close()
    db.close_db()
    for suffix in ("-wal", "-shm"):
        try:
            (data_dir / f"data.db{suffix}").unlink()
        except FileNotFoundError:
            pass

    def _swap(src: Path, dst: Path) -> None:
        incoming = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
        try:
            outgoing = sqlite3.connect(str(dst), timeout=60)
            try:
                incoming.backup(outgoing)
            finally:
                outgoing.close()
        finally:
            incoming.close()

    def _reopen() -> tuple[int, str]:
        db.init_db(config.db_url)
        db.run_migrations(config.db_url)
        fresh = db.Session()
        try:
            settings_mod.seed_defaults(fresh)
            raw_concurrency = settings_mod.get_setting(fresh, "concurrency")
            concurrency = raw_concurrency if isinstance(raw_concurrency, int) else 4
            zone = str(settings_mod.get_setting(fresh, "timezone") or "")
        finally:
            fresh.close()
        clock.set_zone(zone)
        if queue is not None:
            try:
                queue.set_concurrency(concurrency)
            except Exception:
                pass
        return concurrency, zone

    try:
        _swap(source, live)
    except Exception as exc:
        raise RestoreError(f"restore copy failed (live DB untouched): {exc}")
    try:
        _reopen()
    except Exception as exc:
        # Roll back from the safety snapshot, then report.
        try:
            db.close_db()
            _swap(data_dir / BACKUP_DIRNAME / safety["name"], live)
            _reopen()
            raise RestoreError(
                f"restore failed and was rolled back to {safety['name']}: {exc}"
            )
        except RestoreError:
            raise
        except Exception as exc2:
            raise RestoreError(
                f"restore failed AND rollback failed ({exc2}); "
                f"manual recovery from {safety['name']}: {exc}"
            )
    return {"restored": name, "safety_backup": safety["name"]}


class VacuumBusy(Exception):
    """The DB is busy with a live writer — vacuum refused, retry when idle."""


def vacuum(data_dir: Path) -> dict:
    """Checkpoint the WAL and rebuild the DB file; returns size before/after.

    Raises ``VacuumBusy`` (mapped to 409 by the route) when another writer
    holds the DB — the caller should retry when idle instead of 500ing.
    """
    db_path = Path(data_dir) / "data.db"
    if not db_path.is_file():
        raise FileNotFoundError("no database yet")
    before = db_path.stat().st_size
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
    except sqlite3.OperationalError as exc:
        raise VacuumBusy(f"database is busy — retry when idle: {exc}")
    try:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.commit()
        except sqlite3.OperationalError as exc:
            raise VacuumBusy(f"database is busy — retry when idle: {exc}")
    finally:
        conn.close()
    return {"before": before, "after": db_path.stat().st_size}


# -- prune -----------------------------------------------------------------


def _prunable_task_ids(session: Session, cutoff) -> list[int]:
    return list(
        session.execute(
            select(Task.id).where(
                Task.status.in_(PRUNABLE_STATUSES), Task.updated_at < cutoff
            )
        ).scalars()
    )


def _prune_all_mirrors(data_dir: Path) -> None:
    """Best-effort ``git worktree prune`` on every bare mirror.

    Clears stale worktree registrations left when a worktree dir was removed
    plainly (e.g. an orphan whose repo is no longer known). Never raises.
    """
    mirrors = Path(data_dir) / "repos"
    if not mirrors.is_dir():
        return
    try:
        entries = list(mirrors.iterdir())
    except OSError:
        return
    for mirror in entries:
        if not mirror.is_dir():
            continue
        try:
            subprocess.run(
                ["git", "-C", str(mirror), "worktree", "prune"],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            continue


def _orphan_worktree_ids(data_dir: Path, session: Session) -> list[int]:
    """``ws/task-<id>`` dirs with no matching task row (incl. review worktrees)."""
    ws = Path(data_dir) / "ws"
    if not ws.is_dir():
        return []
    ids: set[int] = set()
    try:
        for p in ws.iterdir():
            m = re.fullmatch(r"task-(\d+)(?:-review)?", p.name)
            if m:
                ids.add(int(m.group(1)))
    except OSError:
        return []
    if not ids:
        return []
    live = set(
        session.execute(select(Task.id).where(Task.id.in_(sorted(ids)))).scalars()
    )
    return sorted(ids - live)


def _orphan_screening_ids(data_dir: Path, session: Session) -> list[int]:
    """``ws/screen-<run_id>`` dirs with no matching screening-run row."""
    ws = Path(data_dir) / "ws"
    if not ws.is_dir():
        return []
    ids: set[int] = set()
    try:
        for p in ws.iterdir():
            m = re.fullmatch(r"screen-(\d+)", p.name)
            if m:
                ids.add(int(m.group(1)))
    except OSError:
        return []
    if not ids:
        return []
    live = set(
        session.execute(
            select(ScreeningRun.id).where(ScreeningRun.id.in_(sorted(ids)))
        ).scalars()
    )
    return sorted(ids - live)


def _old_screening_run_ids(session: Session, cutoff) -> list[int]:
    """Terminal screening runs older than the cutoff (prune candidates)."""
    return list(
        session.execute(
            select(ScreeningRun.id).where(
                ScreeningRun.status.in_(("done", "failed")),
                ScreeningRun.finished_at.is_not(None),
                ScreeningRun.finished_at < cutoff,
            )
        ).scalars()
    )


def _orphan_artifact_run_ids(data_dir: Path, session: Session) -> list[int]:
    """Artifact store dirs with no matching run row."""
    store = artifacts.artifact_store_dir(Path(data_dir))
    if not store.is_dir():
        return []
    try:
        ids = {int(p.name) for p in store.iterdir() if p.name.isdigit()}
    except OSError:
        return []
    if not ids:
        return []
    live = set(
        session.execute(select(Run.id).where(Run.id.in_(sorted(ids)))).scalars()
    )
    return sorted(ids - live)


def prune_preview(session: Session, data_dir: Path, older_than_days: int) -> dict:
    """Counts of what a prune would remove (no writes)."""
    cutoff = now() - timedelta(days=max(1, older_than_days))
    task_ids = _prunable_task_ids(session, cutoff)
    run_count = 0
    for chunk in _chunked(task_ids):
        run_count += (
            session.execute(
                select(func.count())
                .select_from(Run)
                .where(Run.task_id.in_(chunk))
            ).scalar()
            or 0
        )
    # Count by task_id (not run_id) to match execute, which deletes
    # ``TaskEvent.task_id IN (task_ids)`` — rows with ``run_id IS NULL``
    # from older code paths are removed too, so preview must count them.
    event_rows = 0
    for chunk in _chunked(task_ids):
        event_rows += (
            session.execute(
                select(func.count())
                .select_from(TaskEvent)
                .where(TaskEvent.task_id.in_(chunk))
            ).scalar()
            or 0
        )
    old_deliveries = (
        session.execute(
            select(func.count())
            .select_from(EventDelivery)
            .where(EventDelivery.received_at < cutoff)
        ).scalar()
        or 0
    )
    old_screening_ids = _old_screening_run_ids(session, cutoff)
    orphan_ws = _orphan_worktree_ids(data_dir, session)
    orphan_art = _orphan_artifact_run_ids(data_dir, session)
    orphan_screen = _orphan_screening_ids(data_dir, session)
    logs_dir = Path(data_dir) / "logs"
    old_logs = 0
    if logs_dir.is_dir():
        try:
            old_logs = sum(
                1
                for p in logs_dir.iterdir()
                if p.is_file() and p.stat().st_mtime < cutoff.timestamp()
            )
        except OSError:
            old_logs = 0
    return {
        "cutoff": cutoff.isoformat(),
        "tasks": {"task_ids": sorted(task_ids), "count": len(task_ids)},
        "runs": run_count,
        "task_events": event_rows,
        "deliveries": old_deliveries,
        "screening_runs": len(old_screening_ids),
        "orphan_worktrees": orphan_ws,
        "orphan_artifacts": orphan_art,
        "orphan_screening_worktrees": orphan_screen,
        "old_logs": old_logs,
    }


def prune_execute(
    session: Session,
    data_dir: Path,
    older_than_days: int,
    scopes: list[str],
    config=None,
) -> dict:
    """Execute a prune for the given scopes; returns what was removed.

    ``config`` (the app Config) is needed for GitWorkspace worktree removal;
    without it, worktree dirs are removed plainly.
    """
    data_dir = Path(data_dir)
    cutoff = now() - timedelta(days=max(1, older_than_days))
    n_tasks = 0
    n_orphan_ws = 0
    n_orphan_art = 0
    n_orphan_screen = 0
    n_deliveries = 0
    n_screening_runs = 0
    n_logs = 0
    if "tasks" in scopes:
        task_ids = _prunable_task_ids(session, cutoff)
        if task_ids:
            repo_names: dict[int, str] = {}
            for chunk in _chunked(task_ids):
                for row in session.execute(
                    select(Task.id, Repo.full_name)
                    .join(Repo, Repo.id == Task.repo_id)
                    .where(Task.id.in_(chunk))
                ).all():
                    repo_names[row[0]] = row[1]
            run_ids = tasks.delete_tasks_cascade(session, task_ids)
            for chunk in _chunked(task_ids):
                session.execute(
                    delete(TaskEvent).where(TaskEvent.task_id.in_(chunk))
                )
            session.commit()
            for run_id in run_ids:
                shutil.rmtree(
                    artifacts.artifact_store_dir(data_dir) / str(run_id),
                    ignore_errors=True,
                )
            for task_id in task_ids:
                full_name = repo_names.get(task_id)
                if config is not None and full_name is not None:
                    try:
                        GitWorkspace(config).remove_review_worktree(
                            task_id, str(full_name)
                        )
                    except Exception:
                        shutil.rmtree(
                            GitWorkspace.review_worktree_path(data_dir, task_id),
                            ignore_errors=True,
                        )
                    try:
                        GitWorkspace(config).remove_worktree(task_id, str(full_name))
                    except Exception:
                        shutil.rmtree(
                            GitWorkspace.worktree_path(data_dir, task_id),
                            ignore_errors=True,
                        )
                else:
                    shutil.rmtree(
                        GitWorkspace.review_worktree_path(data_dir, task_id),
                        ignore_errors=True,
                    )
                    shutil.rmtree(
                        GitWorkspace.worktree_path(data_dir, task_id),
                        ignore_errors=True,
                    )
            n_tasks = len(task_ids)
    if "orphans" in scopes:
        for task_id in _orphan_worktree_ids(data_dir, session):
            shutil.rmtree(
                GitWorkspace.worktree_path(data_dir, task_id), ignore_errors=True
            )
            shutil.rmtree(
                GitWorkspace.review_worktree_path(data_dir, task_id), ignore_errors=True
            )
            n_orphan_ws += 1
        for run_id in _orphan_artifact_run_ids(data_dir, session):
            shutil.rmtree(
                artifacts.artifact_store_dir(data_dir) / str(run_id), ignore_errors=True
            )
            n_orphan_art += 1
        for run_id in _orphan_screening_ids(data_dir, session):
            shutil.rmtree(
                GitWorkspace.screening_worktree_path(data_dir, run_id), ignore_errors=True
            )
            n_orphan_screen += 1
        _prune_all_mirrors(data_dir)
    if "deliveries" in scopes:
        res = session.execute(
            delete(EventDelivery).where(EventDelivery.received_at < cutoff)
        )
        old_run_ids = _old_screening_run_ids(session, cutoff)
        if old_run_ids:
            for chunk in _chunked(old_run_ids):
                session.execute(
                    delete(ScreeningRun).where(ScreeningRun.id.in_(chunk))
                )
        session.commit()
        for run_id in old_run_ids:
            shutil.rmtree(
                GitWorkspace.screening_worktree_path(data_dir, run_id), ignore_errors=True
            )
        n_deliveries = res.rowcount or 0
        n_screening_runs = len(old_run_ids)
    if "logs" in scopes:
        logs_dir = data_dir / "logs"
        if logs_dir.is_dir():
            try:
                for p in logs_dir.iterdir():
                    try:
                        if p.is_file() and p.stat().st_mtime < cutoff.timestamp():
                            p.unlink()
                            n_logs += 1
                    except OSError:
                        continue
            except OSError:
                pass
    return {
        "tasks": n_tasks,
        "orphan_worktrees": n_orphan_ws,
        "orphan_artifacts": n_orphan_art,
        "orphan_screening_worktrees": n_orphan_screen,
        "deliveries": n_deliveries,
        "screening_runs": n_screening_runs,
        "logs": n_logs,
    }
