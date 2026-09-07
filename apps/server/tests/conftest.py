from collections.abc import Generator

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


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(host="127.0.0.1", port=3456, data_dir=tmp_path / "data")


@pytest.fixture
def app(config: Config) -> Generator[Flask]:
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
