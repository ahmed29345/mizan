"""Shared pytest fixtures. pytest finds this file automatically."""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from mizan.config import get_settings
from mizan.db import get_session
from mizan.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # `with` runs the app's lifespan (startup/shutdown) and keeps one event loop for the test,
    # which the async database pool needs.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def anyio_backend() -> str:
    # Async tests (marked @pytest.mark.anyio) run on asyncio, the same loop FastAPI uses.
    return "asyncio"


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A database session whose changes are ALWAYS rolled back after the test.

    How: open a connection, BEGIN a transaction, and give the test a session bound to it.
    join_transaction_mode="create_savepoint" means even `await session.commit()` in the code
    under test only releases a SAVEPOINT; the outer transaction is rolled back at the end.
    Result: tests can't leave rows behind or see each other's data. Needs `alembic upgrade head`.
    """
    # NullPool + a new engine per test: each async test has its own event loop, and pooled
    # asyncpg connections can't move between loops.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def api(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client for API tests whose database writes are rolled back.

    The app's get_session is overridden with the rollback-only db_session, so an endpoint
    that commits still leaves nothing behind. httpx.AsyncClient (not TestClient) keeps the
    request on the same event loop as db_session.
    """

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
