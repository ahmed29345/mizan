"""FastAPI application entry point. Run locally with:  uv run fastapi dev"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mizan.agent.llm import get_chat_model
from mizan.api import ask, cases, health, regulations, search
from mizan.db import engine
from mizan.review.checkpointer import postgres_checkpointer
from mizan.review.graph import build_review_graph


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: open the checkpointer (where paused reviews live) and build the review graph once.
    async with postgres_checkpointer() as checkpointer:
        app.state.review_graph = build_review_graph(get_chat_model(), checkpointer)
        yield
    # Shutdown: close every pooled database connection cleanly.
    await engine.dispose()


app = FastAPI(title="Mizan", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(regulations.router)
app.include_router(search.router)
app.include_router(ask.router)
app.include_router(cases.router)
