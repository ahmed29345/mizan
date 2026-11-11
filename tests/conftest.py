"""Shared pytest fixtures. pytest finds this file automatically."""

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from mizan.config import get_settings
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
