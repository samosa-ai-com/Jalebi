from collections.abc import Generator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import Session

from jalebi import db
from jalebi.app import create_app
from jalebi.config import Config


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(host="127.0.0.1", port=3456, data_dir=tmp_path / "data")


@pytest.fixture
def app(config: Config) -> Generator[Flask]:
    application = create_app(config)
    application.config["TESTING"] = True
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
