"""GET /search: semantic search over indexed regulation chunks."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from mizan.db import SessionDep
from mizan.models import Authority, Language
from mizan.rag.embeddings import EmbedderDep, EmbeddingError
from mizan.schemas.search import SearchResponse
from mizan.services.search import search_chunks

router = APIRouter(tags=["search"])


@router.get("/search", responses={503: {"description": "Embedding service unavailable"}})
async def search(
    session: SessionDep,
    embedder: EmbedderDep,
    q: Annotated[str, Query(min_length=2, max_length=500, description="Question, EN or AR")],
    k: Annotated[int, Query(ge=1, le=20, description="Number of results")] = 5,
    language: Language | None = None,
    authority: Authority | None = None,
) -> SearchResponse:
    try:
        hits = await search_chunks(
            session, embedder, q, k=k, language=language, authority=authority
        )
    except EmbeddingError as error:
        # Our dependency is down, not the client's fault -> 503, and don't leak details.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Search is temporarily unavailable"
        ) from error
    return SearchResponse(query=q, embedding_model=embedder.model_name, hits=hits)
