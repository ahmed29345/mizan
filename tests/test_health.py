"""Tests for GET /health.

pytest runs every function whose name starts with `test_`.
`client` comes from tests/conftest.py: naming it as a parameter makes pytest pass it in.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from mizan.db import get_session
from mizan.main import app


def test_health_ok(client: TestClient) -> None:
    # Uses the real Postgres (docker compose locally, the service container in CI).
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


# ---- Simulating a dead database without stopping Docker ----------------------------------


class BrokenSession:
    """A fake session: same `execute` method as the real one, but it always fails."""

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise ConnectionError("db down")


async def broken_session() -> AsyncIterator[BrokenSession]:
    yield BrokenSession()


@pytest.fixture
def database_down() -> Iterator[None]:
    # dependency_overrides: "when an endpoint asks for get_session, call broken_session instead".
    app.dependency_overrides[get_session] = broken_session
    yield
    # Code after `yield` runs even if the test fails, so the fake never leaks into other tests.
    app.dependency_overrides.clear()


def test_health_db_down(client: TestClient, database_down: None) -> None:
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}
