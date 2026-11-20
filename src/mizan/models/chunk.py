"""Chunks: pieces of article text plus their embedding vectors (the RAG search index).

Why chunks and not whole articles? A real article can be pages long. One vector for pages of
text becomes a blurry average; smaller pieces give sharper matches and shorter LLM prompts.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mizan.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from mizan.models.regulation import Article

# Fixed by the embedding model: bge-m3 outputs 1024 numbers per text.
# Switching to a model with a different size needs a migration AND re-indexing everything.
EMBEDDING_DIMENSIONS = 1024


class Chunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("article_id", "chunk_index"),
        # HNSW = an approximate nearest-neighbour index. Without it, every search compares the
        # question with EVERY chunk (fine for 100 rows, too slow for 1,000,000).
        # vector_cosine_ops: the index is built for cosine distance, which is what we query with.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int]  # 0, 1, 2... order of the chunk inside its article
    content: Mapped[str] = mapped_column(Text)  # the original text shown to users / the LLM
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Which model made this vector. Vectors from different models are NOT comparable,
    # so this tells us what to re-index after a model change.
    embedding_model: Mapped[str] = mapped_column(String(100))

    article: Mapped[Article] = relationship()

    def __repr__(self) -> str:
        return f"<Chunk {self.chunk_index} of article {self.article_id}>"
