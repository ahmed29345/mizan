"""Test doubles: stand-ins for slow, costly or external services."""

import math
import re
import zlib
from collections.abc import Sequence

from mizan.models import EMBEDDING_DIMENSIONS
from mizan.rag.embeddings import EmbeddingError


class FakeEmbedder:
    """Deterministic "bag of words" embedder: each word adds 1 to a fixed slot of the vector.

    Texts sharing words point in similar directions, so ranking behaves sensibly. It is fast,
    free, needs no Ollama (CI has none), and returns the same numbers on every run.
    It does NOT understand meaning or cross-language matches; that's what bge-m3 is for.
    """

    model_name = "fake-bag-of-words"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in re.findall(r"\w+", text.lower()):
            vector[zlib.crc32(word.encode()) % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:  # empty text: avoid a zero vector (cosine of zero vector is undefined)
            vector[0], norm = 1.0, 1.0
        return [x / norm for x in vector]


class BrokenEmbedder:
    """Simulates Ollama being down."""

    model_name = "broken"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("embedding service down")
