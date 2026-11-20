"""POST /ask: ask the compliance agent a question."""

from fastapi import APIRouter, HTTPException, status

from mizan.agent.graph import AgentStepLimitError, answer_question
from mizan.agent.llm import ChatModelDep
from mizan.config import get_settings
from mizan.db import SessionDep
from mizan.rag.embeddings import EmbedderDep, EmbeddingError
from mizan.schemas.ask import AskRequest, AskResponse

router = APIRouter(tags=["agent"])


@router.post("/ask", responses={503: {"description": "AI service unavailable"}})
async def ask(
    payload: AskRequest, session: SessionDep, embedder: EmbedderDep, llm: ChatModelDep
) -> AskResponse:
    try:
        return await answer_question(
            payload.question,
            session=session,
            embedder=embedder,
            llm=llm,
            max_steps=get_settings().agent_max_steps,
        )
    except AgentStepLimitError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="The assistant could not finish an answer"
        ) from error
    except EmbeddingError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Search is temporarily unavailable"
        ) from error
