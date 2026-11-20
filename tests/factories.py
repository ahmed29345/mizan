"""Builders for test data shared by several test files."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from mizan.models import Language, RegulationStatus
from mizan.schemas.regulation import ArticleCreate, RegulationCreate
from mizan.services.indexing import index_regulation
from mizan.services.regulations import create_regulation
from tests.fakes import FakeEmbedder


async def make_indexed_regulation(
    session: AsyncSession,
    embedder: FakeEmbedder,
    status: RegulationStatus = RegulationStatus.ACTIVE,
) -> str:
    code = f"TEST-{uuid.uuid4().hex[:8].upper()}"
    articles = [
        ("1", Language.EN, "Banks must report cyber incidents to the regulator within 24 hours."),
        ("2", Language.EN, "Invoices must be retained for six years in an unalterable format."),
        ("2", Language.AR, "يجب الاحتفاظ بالفواتير لمدة ست سنوات."),
    ]
    await create_regulation(
        session,
        RegulationCreate(
            code=code,
            authority="SAMA",
            title_en="Test Rules",
            status=status,
            articles=[
                ArticleCreate(article_number=n, language=lang, body=body, position=i)
                for i, (n, lang, body) in enumerate(articles)
            ],
        ),
    )
    await index_regulation(session, embedder, code)
    return code


async def make_demo_regulations(session: AsyncSession) -> None:
    """Small versions of the three regulations the review checkers are wired to (AREAS)."""
    from mizan.review.graph import AREAS

    articles = {"cyber": ["1.1", "3.2"], "data": ["5"], "invoice": ["4"]}
    for area, (code, topic) in AREAS.items():
        await create_regulation(
            session,
            RegulationCreate(
                code=code,
                authority="SAMA",
                title_en=f"Test {topic} rules",
                articles=[
                    ArticleCreate(
                        article_number=n, language=Language.EN, body=f"Rule {n}.", position=i
                    )
                    for i, n in enumerate(articles[area])
                ],
            ),
        )
