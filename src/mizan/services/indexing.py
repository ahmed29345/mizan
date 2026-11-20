"""Indexing: regulation -> chunks -> embeddings -> rows in `chunks` (the "ingestion pipeline")."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.models import Article, Chunk, Language
from mizan.rag.chunking import build_embedding_text, chunk_text
from mizan.rag.embeddings import Embedder, EmbeddingError
from mizan.services.regulations import get_regulation


async def index_regulation(session: AsyncSession, embedder: Embedder, code: str) -> int:
    """(Re)build the chunks of one regulation. Returns how many chunks were written.

    Idempotent: old chunks are deleted and new ones inserted in ONE transaction, so search
    never sees a half-indexed regulation, and running it twice gives the same result.
    """
    regulation = await get_regulation(session, code)

    # 1. Chunk every article; build the text to embed (with its context header).
    pending: list[tuple[Article, int, str]] = []  # (article, chunk_index, content)
    texts_to_embed: list[str] = []
    for article in regulation.articles:
        title = (
            regulation.title_ar
            if article.language == Language.AR and regulation.title_ar
            else regulation.title_en
        )
        for index, content in enumerate(chunk_text(article.body)):
            pending.append((article, index, content))
            texts_to_embed.append(
                build_embedding_text(
                    regulation_code=regulation.code,
                    regulation_title=title,
                    article_number=article.article_number,
                    heading=article.heading,
                    content=content,
                )
            )

    # 2. Embed everything (batched inside the embedder). Done BEFORE touching the database,
    #    so if Ollama is down we fail without deleting the old chunks.
    vectors = await embedder.embed(texts_to_embed)
    if len(vectors) != len(pending):
        raise EmbeddingError(f"Expected {len(pending)} vectors, got {len(vectors)}")

    # 3. Replace old chunks with new ones, atomically.
    article_ids = select(Article.id).where(Article.regulation_id == regulation.id)
    await session.execute(delete(Chunk).where(Chunk.article_id.in_(article_ids)))
    session.add_all(
        Chunk(
            article_id=article.id,
            chunk_index=index,
            content=content,
            embedding=vector,
            embedding_model=embedder.model_name,
        )
        for (article, index, content), vector in zip(pending, vectors, strict=True)
    )
    await session.commit()
    return len(pending)
