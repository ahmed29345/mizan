"""Embeddings: turn text into a vector (a list of numbers) that captures its MEANING.

Texts with similar meaning get vectors pointing in similar directions, even with no words in
common, and even across languages: "incident reporting deadline" lands near
"الإبلاغ عن الحوادث خلال ٢٤ ساعة". That is what makes semantic search possible.
"""

from collections.abc import Sequence
from functools import lru_cache
from typing import Annotated, Protocol

import httpx
from fastapi import Depends

from mizan.config import get_settings


class EmbeddingError(Exception):
    """The embedding service failed (not running, model missing, timeout...)."""


class Embedder(Protocol):
    """Anything with this shape can embed text: Ollama now, a cloud API or a fake in tests.

    A Protocol is Python's "interface": classes don't inherit from it, they just need
    matching attributes and methods (structural typing, "duck typing" checked by tools).
    """

    model_name: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Calls a local Ollama server: POST /api/embed {"model": ..., "input": [...]}."""

    def __init__(self, base_url: str, model_name: str, batch_size: int = 32) -> None:
        self.model_name = model_name
        self._batch_size = batch_size
        # One shared HTTP client = connection reuse. Embedding a big batch on CPU can be slow,
        # hence the generous timeout.
        self._client = httpx.AsyncClient(base_url=base_url, timeout=120.0)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        # Send in batches: one request per text is slow; one giant request can time out.
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            try:
                response = await self._client.post(
                    "/api/embed", json={"model": self.model_name, "input": batch}
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise EmbeddingError(
                    f"Embedding with {self.model_name} failed. Is Ollama running "
                    f"and the model pulled (ollama pull {self.model_name})? {error}"
                ) from error
            vectors.extend(response.json()["embeddings"])
        return vectors


@lru_cache
def get_embedder() -> Embedder:
    """FastAPI dependency (one shared instance). Tests override it with a fake embedder."""
    settings = get_settings()
    return OllamaEmbedder(
        settings.ollama_url, settings.embedding_model, settings.embedding_batch_size
    )


EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
