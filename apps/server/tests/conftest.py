import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from jalebi import db
from jalebi.app import create_app
from jalebi.config import Config
from jalebi.db import CatalogAgent, CatalogSkill
from jalebi.seed_catalog import SEED_VERSION_KEY
from jalebi.settings import set_setting


@pytest.fixture(scope="session")
def template_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fully migrated + seeded DB, built once per test session.

    Every ``app`` fixture copies this file instead of re-running 20 Alembic
    migrations + ~50 seed inserts (~0.25s per test). The copy is already at
    head with ``catalog_seed_version`` set, so ``run_migrations`` executes
    nothing and ``seed_catalog`` no-ops; the ``app`` fixture still wipes the
    catalog copy below, so each test starts with an empty library.
    """
    template_dir = tmp_path_factory.mktemp("template") / "data"
    create_app(Config(host="127.0.0.1", port=3456, data_dir=template_dir))
    db.close_db()
    return template_dir / "data.db"


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(host="127.0.0.1", port=3456, data_dir=tmp_path / "data")


@pytest.fixture
def app(config: Config, template_db: Path) -> Generator[Flask]:
    config.ensure_dirs()
    db_path = config.data_dir / "data.db"
    # Copy WAL sidecars too when present — the three files are one snapshot.
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(template_db) + suffix)
        if src.is_file():
            shutil.copy2(src, Path(str(db_path) + suffix))
    application = create_app(config)
    application.config["TESTING"] = True
    # Start every test with an empty skill/agent catalog. ``create_app`` runs
    # the real versioned seeds at startup; tests declare their own slugs and
    # seed-mechanism tests drive ``seed_catalog`` explicitly. (Done here, once
    # per test, rather than in ``session``: pushing another app context from a
    # dependent fixture breaks teardown ordering, and instantiating ``app``
    # for pure-tmp_path tests would create a ``data/`` dir that breaks
    # git-cleanliness tests.)
    with application.app_context():
        s = db.Session()
        s.execute(delete(CatalogSkill))
        s.execute(delete(CatalogAgent))
        s.commit()
        set_setting(s, SEED_VERSION_KEY, 0)
        s.close()
    yield application
    db.close_db()


@pytest.fixture
def client(app) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def session(app) -> Generator[Session]:
    with app.app_context():
        s = db.Session()
        yield s
        s.close()


@pytest.fixture
def engine(app) -> db.Engine:
    return db.get_engine()
