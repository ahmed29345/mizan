"""Regulation business logic. No HTTP here: no status codes, no Request objects.

Why a separate layer? The same functions will be called from:
  - the REST API      (mizan.api.regulations)
  - the seed script   (mizan.seed)
  - AI agents' tools  (week 5: "look up regulation SAMA-CSF")
So the rules live in one place. Errors are plain Python exceptions; each caller decides
how to present them (the API turns them into 404 / 409).
"""

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mizan.models import Article, Authority, Regulation, RegulationStatus
from mizan.schemas.regulation import RegulationCreate


class RegulationNotFoundError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"Regulation {code} not found")
        self.code = code


class DuplicateRegulationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"Regulation {code} already exists")
        self.code = code


async def create_regulation(session: AsyncSession, data: RegulationCreate) -> Regulation:
    regulation = Regulation(**data.model_dump(exclude={"articles"}))
    regulation.articles = [Article(**article.model_dump()) for article in data.articles]
    session.add(regulation)
    try:
        # Don't check "does this code exist?" first and then insert: two requests at the same
        # moment could both pass the check. Let the UNIQUE constraint decide, then translate.
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if "uq_regulations_code" in str(error.orig):
            raise DuplicateRegulationError(data.code) from error
        raise
    return await get_regulation(session, data.code)


async def get_regulation(session: AsyncSession, code: str) -> Regulation:
    regulation = await session.scalar(
        select(Regulation)
        .where(Regulation.code == code.upper())
        # Load articles in the same round trip (async SQLAlchemy can't lazy-load later).
        .options(selectinload(Regulation.articles))
        # Refresh from the DB even if this object is already in the session's memory.
        .execution_options(populate_existing=True)
    )
    if regulation is None:
        raise RegulationNotFoundError(code)
    return regulation


async def list_regulations(
    session: AsyncSession,
    *,
    authority: Authority | None = None,
    status: RegulationStatus | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Regulation], int]:
    """Return one page of regulations plus the total count matching the filters."""
    query = select(Regulation)
    if authority is not None:
        query = query.where(Regulation.authority == authority)
    if status is not None:
        query = query.where(Regulation.status == status)
    if search:
        # ILIKE = case-insensitive LIKE. SQLAlchemy sends `pattern` as a bound parameter,
        # never pasted into the SQL string, so this is safe from SQL injection.
        pattern = f"%{search}%"
        query = query.where(
            or_(
                Regulation.code.ilike(pattern),
                Regulation.title_en.ilike(pattern),
                Regulation.title_ar.ilike(pattern),
            )
        )

    # Count with the same filters, before applying the page window.
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(
        # Always order before paginating, or Postgres may return rows in any order,
        # and the same row could appear on page 1 and page 2.
        query.order_by(Regulation.code).limit(limit).offset(offset)
    )
    return list(rows), total
