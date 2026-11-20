"""Search result shapes, shared by the API now and the agents' tools later."""

import uuid

from pydantic import BaseModel

from mizan.models import Authority, Language


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    regulation_code: str
    regulation_title: str
    authority: Authority
    article_number: str
    heading: str | None
    language: Language
    content: str
    # Cosine similarity, 1.0 = same meaning, ~0 = unrelated. Useful for ranking and for
    # "no good answer" thresholds later; not a calibrated probability.
    score: float


class SearchResponse(BaseModel):
    query: str
    embedding_model: str
    hits: list[SearchHit]
