"""Build the search index for every regulation.

Run:  uv run python -m mizan.index
Needs Ollama running with the embedding model pulled (ollama pull bge-m3).
"""

import asyncio
import time

from sqlalchemy import select

from mizan.db import SessionLocal, engine
from mizan.models import Regulation
from mizan.rag.embeddings import get_embedder
from mizan.services.indexing import index_regulation


async def main() -> None:
    embedder = get_embedder()
    async with SessionLocal() as session:
        codes = list(await session.scalars(select(Regulation.code).order_by(Regulation.code)))
        for code in codes:
            started = time.perf_counter()
            count = await index_regulation(session, embedder, code)
            print(f"  {code:<18} {count:>3} chunks  {time.perf_counter() - started:.1f}s")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
