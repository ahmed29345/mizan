"""Alembic runs this file for every command (upgrade, downgrade, revision --autogenerate, check).

Its two jobs: tell Alembic WHICH database (DATABASE_URL from our settings) and WHAT the
tables should look like (Base.metadata from our models), so autogenerate can compare them.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from mizan.config import get_settings
from mizan.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)  # logging settings from alembic.ini

# The "desired state": every table defined by our models.
target_metadata = Base.metadata


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Tell autogenerate / `alembic check` to ignore tables we don't own.

    LangGraph's Postgres checkpointer creates its own tables (checkpoints, checkpoint_writes...)
    to save paused graphs. Without this filter, Alembic would see them as "not in our models"
    and try to DROP them in the next migration.
    """
    return not (type_ == "table" and reflected and (name or "").startswith("checkpoint"))


# One source of truth for the URL: .env locally, environment variables in CI / production.
DATABASE_URL = get_settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it (useful for DBA review)."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, include_object=include_object
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: a migration is a one-off script, so open one connection and close it; no pool.
    engine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        # Alembic itself is synchronous; run_sync runs it on top of our async connection.
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
