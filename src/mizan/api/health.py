"""Health check endpoint.

A load balancer or Kubernetes calls GET /health every few seconds to ask "is this app OK?".
  - Database reachable   -> 200  {"status": "ok",       "database": "ok"}
  - Database unreachable -> 503  {"status": "degraded", "database": "unreachable"}
503 ("Service Unavailable") tells the load balancer to stop sending users to this instance.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from mizan.db import SessionDep

logger = logging.getLogger(__name__)

# A router groups related endpoints; main.py plugs it into the app with include_router().
# tags=["health"] only groups the endpoint under a "health" heading in /docs.
router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: SessionDep) -> dict[str, str]:
    # SessionDep (see db.py): FastAPI calls get_session() before this function runs,
    # passes the session in, and closes it after the response is sent.
    try:
        # The cheapest possible query: it proves the connection works without reading any table.
        # `await` = while waiting for Postgres, the server is free to handle other requests.
        await session.execute(text("SELECT 1"))
    except Exception:
        # Catch Exception, not a bare `except:` — a bare except also swallows Ctrl+C and shutdown.
        # Log the details for us; return only a short message to the caller (don't leak internals).
        logger.exception("Health check: database unreachable")
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": "unreachable"},
        )

    # Returning a dict -> FastAPI turns it into JSON with status 200.
    return {"status": "ok", "database": "ok"}
