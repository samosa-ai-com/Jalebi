from pathlib import Path

import pytest

from jalebi.config import Config, _is_repo_root, load_config, repo_root


def test_config_defaults() -> None:
    cfg = Config()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 2052
    assert cfg.data_dir == Path.home() / ".jalebi"
    assert cfg.db_url == f"sqlite:///{cfg.data_dir.as_posix()}/data.db"


def test_config_port_junk_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("JALEBI_PORT", "not-a-number")
    assert load_config().port == 2052


def test_config_db_url_rejects_url_breaking_chars() -> None:
    with pytest.raises(ValueError, match="JALEBI_DATA_DIR"):
        _ = Config(data_dir=Path("/tmp/a?b")).db_url


def test_config_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("JALEBI_HOST", "0.0.0.0")
    monkeypatch.setenv("JALEBI_PORT", "9000")
    monkeypatch.setenv("JALEBI_DATA_DIR", "/tmp/jalebi-env-test")
    cfg = load_config()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000
    assert cfg.data_dir == Path("/tmp/jalebi-env-test")


def test_ensure_dirs_creates_data_dirs(config: Config) -> None:
    config.ensure_dirs()
    for sub in ("", "repos", "ws", "agents", "logs"):
        assert (config.data_dir / sub).is_dir()


def test_repo_root_points_at_repo() -> None:
    root = repo_root()
    assert (root / "package.json").is_file()


def test_is_repo_root_accepts_linked_worktree(tmp_path) -> None:
    """A linked git worktree has ``.git`` as a FILE, not a directory — the SPA
    serve path must still resolve the repo root (previously ``.is_dir()`` missed it)."""
    root = tmp_path / "worktree"
    root.mkdir()
    (root / "package.json").write_text("{}")
    (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    assert _is_repo_root(root)
    # Without the git marker it is not a repo root.
    (root / ".git").unlink()
    assert not _is_repo_root(root)
