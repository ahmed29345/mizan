"""Database engine and the per-request session dependency.

- The *engine* owns a pool of connections and lives for the whole app.
- A *session* is borrowed for one request and returned to the pool afterwards.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mizan.config import get_settings

# pool_pre_ping: test a pooled connection before using it, so a restarted DB doesn't break requests.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that lends one session per request (use SessionDep below).

    Code before `yield` runs before the endpoint; the `async with` closes the session after it.
    """
    async with SessionLocal() as session:
        yield session


# Reusable type for endpoints: `async def endpoint(session: SessionDep)`.
# Annotated[Type, Depends(...)] = "this parameter is an AsyncSession, and FastAPI provides it".
SessionDep = Annotated[AsyncSession, Depends(get_session)]
