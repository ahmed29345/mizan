"""Agent tests with a scripted fake LLM + fake embedder: no Ollama, fully deterministic."""

import httpx
import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.agent.graph import AgentStepLimitError, answer_question, extract_citations
from mizan.agent.llm import get_chat_model
from mizan.main import app
from mizan.rag.embeddings import get_embedder
from tests.factories import make_indexed_regulation
from tests.fakes import FakeEmbedder, ScriptedChatModel, tool_call

pytestmark = pytest.mark.anyio

QUESTION = "report cyber incidents regulator"  # shares words with article 1 (FakeEmbedder)


def tool_results_seen(llm: ScriptedChatModel, turn: int) -> str:
    """All tool output the fake LLM had in front of it on a given turn."""
    return "\n".join(str(m.content) for m in llm.received[turn] if isinstance(m, ToolMessage))


async def test_question_is_always_searched_before_the_llm_answers(
    db_session: AsyncSession,
) -> None:
    embedder = FakeEmbedder()
    code = await make_indexed_regulation(db_session, embedder)
    llm = ScriptedChatModel(replies=[f"Report within 24 hours [{code} Art. 1]."])

    result = await answer_question(
        QUESTION, session=db_session, embedder=embedder, llm=llm, max_steps=4
    )

    # The retrieve node searched the exact question before the model's first turn...
    assert result.tool_calls[0].tool == "search_regulations"
    assert result.tool_calls[0].args == {"query": QUESTION}
    assert "within 24 hours" in tool_results_seen(llm, turn=0)
    # ...and the citation is verified because that search returned the article.
    assert result.answer == f"Report within 24 hours [{code} Art. 1]."
    assert [(c.regulation_code, c.article_number, c.verified) for c in result.citations] == [
        (code, "1", True)
    ]
    assert result.llm_turns == 1


async def test_citation_the_agent_never_retrieved_is_flagged(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    await make_indexed_regulation(db_session, embedder)
    llm = ScriptedChatModel(replies=["Banks need 1 billion riyals [FAKE-REG Art. 9]."])

    result = await answer_question(
        QUESTION, session=db_session, embedder=embedder, llm=llm, max_steps=4
    )

    assert result.citations[0].regulation_code == "FAKE-REG"
    assert result.citations[0].verified is False  # hallucinated citation caught


async def test_agent_recovers_from_a_tool_error(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    code = await make_indexed_regulation(db_session, embedder)
    llm = ScriptedChatModel(
        replies=[
            # Turn 1: the "model" asks for a regulation that doesn't exist...
            tool_call("get_article", regulation_code="NOPE", article_number="1"),
            # Turn 2: ...reads the error, then asks for the right one...
            tool_call("get_article", "call-2", regulation_code=code, article_number="2"),
            # Turn 3: ...and answers.
            f"Keep invoices six years [{code} Art. 2].",
        ]
    )

    result = await answer_question(
        "invoice retention", session=db_session, embedder=embedder, llm=llm, max_steps=4
    )

    assert "NOPE does not exist" in tool_results_seen(llm, turn=1)  # error became a message
    assert "six years" in tool_results_seen(llm, turn=2)
    assert result.citations[0].verified is True  # get_article also counts as retrieval
    assert result.llm_turns == 3
    assert [c.tool for c in result.tool_calls] == [
        "search_regulations",
        "get_article",
        "get_article",
    ]


async def test_endless_tool_calling_hits_the_step_limit(db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    await make_indexed_regulation(db_session, embedder)
    llm = ScriptedChatModel(replies=[tool_call("search_regulations", query="again")])  # forever

    with pytest.raises(AgentStepLimitError):
        await answer_question(QUESTION, session=db_session, embedder=embedder, llm=llm, max_steps=2)

    assert len(llm.received) == 2  # stopped after max_steps LLM turns


def test_extract_citations_dedupes_and_checks_retrieval() -> None:
    answer = "يجب ذلك [DEMO-PDPL Art. 29]. Also [DEMO-PDPL Art 29] and [DEMO-X Art. 3.2]."

    citations = extract_citations(answer, retrieved={("DEMO-PDPL", "29")})

    assert [(c.regulation_code, c.article_number, c.verified) for c in citations] == [
        ("DEMO-PDPL", "29", True),
        ("DEMO-X", "3.2", False),
    ]


async def test_ask_api(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    code = await make_indexed_regulation(db_session, embedder)
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_chat_model] = lambda: ScriptedChatModel(
        replies=[f"Within 24 hours [{code} Art. 1]."]
    )

    response = await api.post("/ask", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "scripted-fake"
    assert body["citations"] == [{"regulation_code": code, "article_number": "1", "verified": True}]


async def test_ask_api_step_limit_returns_503(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    embedder = FakeEmbedder()
    await make_indexed_regulation(db_session, embedder)
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_chat_model] = lambda: ScriptedChatModel(
        replies=[tool_call("search_regulations", query="again")]
    )

    response = await api.post("/ask", json={"question": QUESTION})

    assert response.status_code == 503


async def test_ask_api_rejects_tiny_question(api: httpx.AsyncClient) -> None:
    response = await api.post("/ask", json={"question": "?"})

    assert response.status_code == 422
