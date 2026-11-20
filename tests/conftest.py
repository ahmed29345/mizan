"""Shared pytest fixtures. pytest finds this file automatically.

Tests use their OWN database (<name>_test, e.g. mizan_test), never your dev database.
It is created if missing and migrated to the latest version once per test run.
"""

# ---- 1. Point the app at the test database BEFORE any mizan module creates the engine ----
import asyncio
import os
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from mizan.config import Settings, get_settings

_dev_url = make_url(Settings().database_url)  # from .env locally, env vars in CI
TEST_DATABASE_URL = _dev_url.set(database=f"{_dev_url.database}_test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL.render_as_string(hide_password=False)
# Tests never send traces, even if .env has Langfuse keys (empty keys = tracing off).
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
get_settings.cache_clear()  # forget any settings read before the override

# ---- 2. Normal imports (these read the overridden DATABASE_URL) ----
from collections.abc import AsyncIterator, Iterator  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from mizan.db import get_session  # noqa: E402
from mizan.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


async def _create_test_database() -> None:
    # CREATE DATABASE can't run inside the target DB, so connect to the built-in "postgres" DB.
    connection = await asyncpg.connect(
        host=TEST_DATABASE_URL.host,
        port=TEST_DATABASE_URL.port,
        user=TEST_DATABASE_URL.username,
        password=TEST_DATABASE_URL.password,
        database="postgres",
    )
    try:
        name = TEST_DATABASE_URL.database
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session", autouse=True)
def test_database() -> None:
    """Once per test run: create mizan_test if needed, then `alembic upgrade head` on it.

    Running the real migrations (not create_all) also tests the migrations themselves.
    """
    asyncio.run(_create_test_database())
    command.upgrade(Config(toml_file=ROOT / "pyproject.toml"), "head")


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
