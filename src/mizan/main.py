"""FastAPI application entry point. Run locally with:  uv run fastapi dev"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from mizan.agent.llm import get_chat_model
from mizan.api import ask, cases, health, meta, regulations, search
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
app.include_router(meta.router)

# The web UI: static HTML/CSS/JS served by the same app (same origin: no CORS needed).
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="ui")


@app.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    return RedirectResponse("/ui/")
