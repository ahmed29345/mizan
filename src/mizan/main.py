"""FastAPI application entry point. Run locally with:  uv run fastapi dev"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mizan.api import health, regulations, search
from mizan.db import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup code goes before `yield` (e.g. warm caches, load models in later weeks).
    yield
    # Shutdown: close every pooled database connection cleanly.
    await engine.dispose()


app = FastAPI(title="Mizan", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(regulations.router)
app.include_router(search.router)
