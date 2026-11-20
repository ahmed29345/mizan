"""Tools the LLM can call. Each is a thin wrapper around a service we already have.

The LLM sees each tool's NAME, DOCSTRING and ARGUMENTS (as a JSON schema) and decides by
itself when to call it. So the docstring is part of the prompt: write it for the model.

Tools return plain TEXT, because the result goes back into the LLM's context.
Errors become text too ("not found"), so the agent can recover instead of crashing.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.rag.embeddings import Embedder
from mizan.services.regulations import RegulationNotFoundError, get_regulation
from mizan.services.search import search_chunks


@dataclass
class RetrievalLog:
    """Every article the tools showed the LLM in this run.

    Used afterwards to check the answer's citations: citing an article the agent never
    retrieved means the model made it up (a hallucinated citation).
    """

    articles: set[tuple[str, str]] = field(default_factory=set)  # (code, article_number)


def build_tools(session: AsyncSession, embedder: Embedder, log: RetrievalLog) -> list[BaseTool]:
    # The LLM may request several tools at once and LangGraph runs them concurrently.
    # One AsyncSession must never be used by two tasks at the same time, so take turns.
    db_lock = asyncio.Lock()

    @tool
    async def search_regulations(query: str) -> str:
        """Search the regulation library by meaning. Works in English and Arabic.

        Args:
            query: What to look for, e.g. "deadline to report cyber incidents".
        """
        # Deliberately NO filters (authority, language) here. Small models fill optional
        # arguments with guesses (e.g. authority="SAMA" for a data-protection question),
        # which silently hides the right answer. Fewer arguments = fewer wrong guesses.
        async with db_lock:
            hits = await search_chunks(session, embedder, query, k=6)
        if not hits:
            return "No matching articles found."
        lines = []
        for hit in hits:
            log.articles.add((hit.regulation_code, hit.article_number))
            heading = f" - {hit.heading}" if hit.heading else ""
            lines.append(
                f"[{hit.regulation_code} Art. {hit.article_number}] ({hit.language.value}, "
                f"{hit.authority.value}){heading} (relevance {hit.score:.2f})\n{hit.content}"
            )
        return "\n\n".join(lines)

    @tool
    async def get_article(
        regulation_code: str, article_number: str, language: Literal["en", "ar"] = "en"
    ) -> str:
        """Read the full text of one article, e.g. regulation_code="DEMO-PDPL", article_number="29".

        Args:
            regulation_code: The regulation code shown in search results.
            article_number: The article number shown in search results.
            language: "en" or "ar".
        """
        try:
            async with db_lock:
                regulation = await get_regulation(session, regulation_code)
        except RegulationNotFoundError:
            # Same service error the API turns into a 404; here it becomes a message the
            # LLM can read and react to (e.g. search again).
            return f"Regulation {regulation_code} does not exist. Use search_regulations."
        for article in regulation.articles:
            if article.article_number == article_number and article.language.value == language:
                log.articles.add((regulation.code, article.article_number))
                return (
                    f"[{regulation.code} Art. {article.article_number}] "
                    f"{article.heading or ''}\n{article.body}"
                )
        numbers = sorted({a.article_number for a in regulation.articles})
        return f"Article {article_number} not found in {regulation.code}. Articles: {numbers}"

    return [search_regulations, get_article]
