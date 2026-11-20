"""Indexing + semantic search, using FakeEmbedder (no Ollama needed)."""

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.main import app
from mizan.models import Chunk, Language, RegulationStatus
from mizan.rag.embeddings import EmbeddingError, get_embedder
from mizan.services.indexing import index_regulation
from mizan.services.search import search_chunks
from tests.factories import make_indexed_regulation
from tests.fakes import BrokenEmbedder, FakeEmbedder

pytestmark = pytest.mark.anyio


async def count_chunks(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Chunk)) or 0


async def test_search_returns_most_relevant_article_first(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    code = await make_indexed_regulation(db_session, embedder)

    # FakeEmbedder matches exact words only ("retain" != "retained"), so reuse the article's words.
    hits = await search_chunks(db_session, embedder, "invoices retained six years", k=2)

    assert hits[0].regulation_code == code
    assert hits[0].article_number == "2"
    assert hits[0].language == Language.EN
    assert hits[0].score > hits[1].score


async def test_language_filter(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    await make_indexed_regulation(db_session, embedder)

    hits = await search_chunks(db_session, embedder, "الفواتير", language=Language.AR)

    assert hits
    assert all(hit.language == Language.AR for hit in hits)


async def test_superseded_regulations_are_never_returned(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    await make_indexed_regulation(db_session, embedder, status=RegulationStatus.SUPERSEDED)

    assert await search_chunks(db_session, embedder, "report cyber incidents") == []


async def test_reindexing_replaces_chunks_instead_of_duplicating(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    code = await make_indexed_regulation(db_session, embedder)
    before = await count_chunks(db_session)

    await index_regulation(db_session, embedder, code)

    assert await count_chunks(db_session) == before == 3


async def test_failed_embedding_keeps_old_chunks(db_session: AsyncSession) -> None:
    code = await make_indexed_regulation(db_session, FakeEmbedder())

    with pytest.raises(EmbeddingError):
        await index_regulation(db_session, BrokenEmbedder(), code)

    assert await count_chunks(db_session) == 3  # old index still usable


async def test_search_api(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    await make_indexed_regulation(db_session, embedder)
    app.dependency_overrides[get_embedder] = lambda: embedder

    response = await api.get("/search", params={"q": "cyber incidents regulator", "k": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["embedding_model"] == "fake-bag-of-words"
    assert body["hits"][0]["article_number"] == "1"


async def test_search_api_returns_503_when_embeddings_are_down(api: httpx.AsyncClient) -> None:
    app.dependency_overrides[get_embedder] = BrokenEmbedder

    response = await api.get("/search", params={"q": "anything"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Search is temporarily unavailable"}
