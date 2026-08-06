from collections.abc import Generator

import pytest
from flask.testing import FlaskClient

from jalebi.app import create_app


@pytest.fixture
def client() -> Generator[FlaskClient]:
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client
