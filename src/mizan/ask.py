"""Ask the agent from the terminal.

Run:  uv run python -m mizan.ask "How fast must a bank report a cyber incident?"
"""

import asyncio
import sys

from mizan.agent.graph import answer_question
from mizan.agent.llm import get_chat_model
from mizan.config import get_settings
from mizan.db import SessionLocal, engine
from mizan.rag.embeddings import get_embedder


async def main(question: str) -> None:
    async with SessionLocal() as session:
        result = await answer_question(
            question,
            session=session,
            embedder=get_embedder(),
            llm=get_chat_model(),
            max_steps=get_settings().agent_max_steps,
        )
    await engine.dispose()

    print(f"\nQ: {result.question}\n\n{result.answer}\n")
    if result.blocked_by:
        print(f"  (blocked by guardrail: {result.blocked_by})")
    for citation in result.citations:
        mark = "verified" if citation.verified else "NOT RETRIEVED - possible hallucination"
        print(f"  [{citation.regulation_code} Art. {citation.article_number}]  {mark}")
    print("\nSteps:")
    for call in result.tool_calls:
        print(f"  -> {call.tool}({call.args})")
    print(f"\n{result.model}, {result.llm_turns} LLM turns, {result.duration_ms / 1000:.1f}s")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: uv run python -m mizan.ask "your question"')
    asyncio.run(main(" ".join(sys.argv[1:])))
