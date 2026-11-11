"""Tests for the Regulation and Article models against the real (migrated) database."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mizan.models import Article, Authority, Language, Regulation, RegulationStatus

# Every test in this file is async and runs on the anyio plugin (see anyio_backend in conftest).
pytestmark = pytest.mark.anyio


def make_regulation(**overrides: object) -> Regulation:
    """Test data builder: valid defaults, override only what the test cares about."""
    fields: dict[str, object] = {
        # Unique code per call so tests never clash with each other or with dev data.
        "code": f"TEST-{uuid.uuid4().hex[:8]}",
        "authority": Authority.SAMA,
        "title_en": "Example Cyber Security Framework (fictional)",
        "title_ar": "إطار الأمن السيبراني (مثال)",
    }
    fields.update(overrides)
    return Regulation(**fields)


async def test_regulation_with_bilingual_articles(db_session: AsyncSession) -> None:
    regulation = make_regulation()
    # Added out of order on purpose: `position` must decide the reading order.
    regulation.articles = [
        Article(article_number="2.1", language=Language.EN, body="Second article.", position=2),
        Article(article_number="1.1", language=Language.EN, body="First article.", position=1),
        Article(article_number="1.1", language=Language.AR, body="المادة الأولى.", position=1),
    ]
    db_session.add(regulation)
    await db_session.flush()  # send INSERTs to Postgres (inside the test transaction)
    db_session.expunge_all()  # forget cached objects, so the next query really hits the DB

    # selectinload: fetch the articles in the same call. In async code, lazy loading
    # (touching regulation.articles later) raises an error instead of silently querying.
    loaded = await db_session.scalar(
        select(Regulation)
        .where(Regulation.code == regulation.code)
        .options(selectinload(Regulation.articles))
    )

    assert loaded is not None
    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.status == RegulationStatus.ACTIVE  # Python-side default
    assert loaded.version == "1.0"
    assert loaded.created_at is not None  # filled by Postgres now()
    assert [a.position for a in loaded.articles] == [1, 1, 2]
    assert {a.language for a in loaded.articles} == {Language.EN, Language.AR}


async def test_same_article_number_and_language_is_rejected(db_session: AsyncSession) -> None:
    regulation = make_regulation()
    regulation.articles = [
        Article(article_number="1.1", language=Language.EN, body="Original.", position=1),
        Article(article_number="1.1", language=Language.EN, body="Duplicate!", position=2),
    ]
    db_session.add(regulation)

    # The UNIQUE (regulation_id, article_number, language) constraint stops it in the database.
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_database_rejects_unknown_authority(db_session: AsyncSession) -> None:
    # Raw SQL bypasses our Python enum entirely; the CHECK constraint must still block it.
    with pytest.raises(IntegrityError, match="ck_regulations_authority"):
        await db_session.execute(
            text(
                "INSERT INTO regulations (id, code, authority, title_en, version, status) "
                "VALUES (gen_random_uuid(), 'BAD-1', 'NOT_A_REGULATOR', 'x', '1.0', 'active')"
            )
        )


async def test_deleting_regulation_deletes_its_articles(db_session: AsyncSession) -> None:
    regulation = make_regulation()
    regulation.articles = [
        Article(article_number="1", language=Language.EN, body="Text.", position=1),
    ]
    db_session.add(regulation)
    await db_session.flush()
    regulation_id = regulation.id

    await db_session.delete(regulation)
    await db_session.flush()

    remaining = await db_session.scalar(
        select(Article.id).where(Article.regulation_id == regulation_id)
    )
    assert remaining is None
