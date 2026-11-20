"""Compliance review cases: create, run the multi-agent review, approve or return."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from langgraph.graph.state import CompiledStateGraph

from mizan.db import SessionDep
from mizan.schemas.case import CaseCreate, CaseRead, DecisionRequest
from mizan.services import cases as service

router = APIRouter(prefix="/cases", tags=["cases"])


def get_review_graph(request: Request) -> CompiledStateGraph:
    """Built once at startup (main.py lifespan) with the Postgres checkpointer."""
    return request.app.state.review_graph


ReviewGraphDep = Annotated[CompiledStateGraph, Depends(get_review_graph)]


def _http_errors(error: Exception) -> HTTPException:
    if isinstance(error, service.CaseNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail="Case not found")
    return HTTPException(status.HTTP_409_CONFLICT, detail=str(error))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(payload: CaseCreate, session: SessionDep) -> CaseRead:
    return CaseRead.model_validate(await service.create_case(session, payload))


@router.get("/{case_id}", responses={404: {"description": "Case not found"}})
async def get_case(case_id: uuid.UUID, session: SessionDep) -> CaseRead:
    try:
        return CaseRead.model_validate(await service.get_case(session, case_id))
    except service.CaseNotFoundError as error:
        raise _http_errors(error) from error


@router.post("/{case_id}/review", responses={404: {}, 409: {"description": "Not a draft"}})
async def review_case(case_id: uuid.UUID, session: SessionDep, graph: ReviewGraphDep) -> CaseRead:
    """Run the AI agents. Slow (about 1-2 minutes locally): returns when the report is ready."""
    try:
        case = await service.start_review(session, graph, case_id)
    except (service.CaseNotFoundError, service.InvalidCaseStateError) as error:
        raise _http_errors(error) from error
    return CaseRead.model_validate(case)


@router.post("/{case_id}/decision", responses={404: {}, 409: {"description": "Not awaiting"}})
async def decide_case(
    case_id: uuid.UUID, payload: DecisionRequest, session: SessionDep, graph: ReviewGraphDep
) -> CaseRead:
    """The compliance officer approves the report, or returns it with a comment."""
    try:
        case = await service.decide(session, graph, case_id, payload)
    except (service.CaseNotFoundError, service.InvalidCaseStateError) as error:
        raise _http_errors(error) from error
    return CaseRead.model_validate(case)
