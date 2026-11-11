"""Health check endpoint.

============================ YOUR TASK (week 1) ============================

Write  GET /health  so a load balancer / Kubernetes can ask "is this app OK?"

Behaviour:
  - Database reachable   -> 200  {"status": "ok",       "database": "ok"}
  - Database unreachable -> 503  {"status": "degraded", "database": "unreachable"}

Hints (try first, then look up the docs):
  1. Decorate an `async def` function with  @router.get("/health")
  2. Ask FastAPI for a DB session by adding a parameter:
         session: AsyncSession = Depends(get_session)
     (import AsyncSession from sqlalchemy.ext.asyncio, get_session from mizan.db)
  3. Prove the DB answers:   await session.execute(text("SELECT 1"))
     (import `text` from sqlalchemy)
  4. If that raises an exception, return a 503. Two ways:
       - return JSONResponse(status_code=503, content={...})   (fastapi.responses)
       - or add a `response: Response` parameter and set response.status_code = 503
     Catch `Exception` here, not a bare `except:`; ruff will tell you why.

Stretch goal: also report whether the `vector` extension is installed
  (SELECT extversion FROM pg_extension WHERE extname = 'vector').

Check your work:
  uv run fastapi dev        -> open http://127.0.0.1:8000/health and /docs
  uv run ruff check .       -> lint
  uv run pytest             -> tests (after you write tests/test_health.py)
============================================================================
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


# TODO(you): write the GET /health endpoint here.
