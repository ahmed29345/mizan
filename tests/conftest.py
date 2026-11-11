"""Shared pytest fixtures. pytest finds this file automatically."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mizan.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # `with` runs the app's lifespan (startup/shutdown) and keeps one event loop for the test,
    # which the async database pool needs.
    with TestClient(app) as test_client:
        yield test_client
