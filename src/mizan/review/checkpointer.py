"""Where paused reviews are saved: Postgres, via LangGraph's checkpointer.

Every graph step writes a "checkpoint" (the full state) keyed by thread_id (= case id).
When the graph pauses for the human, the checkpoint is what survives: the server can restart
and the review still resumes exactly where it stopped.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from mizan.config import get_settings


@asynccontextmanager
async def postgres_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    # LangGraph's saver uses the psycopg driver, so drop SQLAlchemy's "+asyncpg" part.
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    async with AsyncPostgresSaver.from_conn_string(
        url.render_as_string(hide_password=False)
    ) as saver:
        await saver.setup()  # creates its tables once (ignored by Alembic, see migrations/env.py)
        yield saver
