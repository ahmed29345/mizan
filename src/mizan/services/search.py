"""Semantic search: embed the question, return the closest chunks (the "R" in RAG)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.models import Article, Authority, Chunk, Language, Regulation, RegulationStatus
from mizan.rag.embeddings import Embedder
from mizan.schemas.search import SearchHit


async def search_chunks(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    *,
    k: int = 5,
    language: Language | None = None,
    authority: Authority | None = None,
) -> list[SearchHit]:
    [query_vector] = await embedder.embed([query])

    # Cosine DISTANCE (pgvector's <=> operator): 0 = same direction, 2 = opposite.
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    stmt = (
        select(Chunk, Article, Regulation, distance)
        .join(Article, Chunk.article_id == Article.id)
        .join(Regulation, Article.regulation_id == Regulation.id)
        # Only current rules: a superseded regulation must never be cited as the answer.
        .where(Regulation.status == RegulationStatus.ACTIVE)
        # Only compare vectors made by the SAME model as the query vector.
        .where(Chunk.embedding_model == embedder.model_name)
        .order_by(distance)  # nearest first; this ORDER BY + LIMIT is what HNSW speeds up
        .limit(k)
    )
    if language is not None:
        stmt = stmt.where(Article.language == language)
    if authority is not None:
        stmt = stmt.where(Regulation.authority == authority)

    rows = await session.execute(stmt)
    return [
        SearchHit(
            chunk_id=chunk.id,
            regulation_code=regulation.code,
            regulation_title=(
                regulation.title_ar
                if article.language == Language.AR and regulation.title_ar
                else regulation.title_en
            ),
            authority=regulation.authority,
            article_number=article.article_number,
            heading=article.heading,
            language=article.language,
            content=chunk.content,
            score=round(1 - dist, 4),  # similarity = 1 - distance, easier to read
        )
        for chunk, article, regulation, dist in rows
    ]
