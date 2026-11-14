"""HTTP layer for regulations: parse the request, call the service, shape the response.

Endpoints stay thin. Validation happens in the schemas, rules in the service;
this file only translates between HTTP and Python.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from mizan.db import SessionDep
from mizan.models import Authority, RegulationStatus
from mizan.schemas.regulation import (
    Page,
    RegulationCreate,
    RegulationRead,
    RegulationSummary,
)
from mizan.services import regulations as service

# prefix: every path below starts with /regulations. tags: grouping in /docs.
router = APIRouter(prefix="/regulations", tags=["regulations"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "A regulation with this code already exists"}},
)
async def create_regulation(payload: RegulationCreate, session: SessionDep) -> RegulationRead:
    # `payload: RegulationCreate` -> FastAPI reads the JSON body and validates it (422 if bad)
    # before this function even runs.
    try:
        regulation = await service.create_regulation(session, payload)
    except service.DuplicateRegulationError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    return RegulationRead.model_validate(regulation)


@router.get("")
async def list_regulations(
    session: SessionDep,
    # Query(...) = read from the URL: /regulations?authority=SAMA&limit=10
    authority: Authority | None = None,
    regulation_status: Annotated[RegulationStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(min_length=2, max_length=100, description="Search")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,  # cap it: nobody downloads 1M rows
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RegulationSummary]:
    rows, total = await service.list_regulations(
        session,
        authority=authority,
        status=regulation_status,
        search=q,
        limit=limit,
        offset=offset,
    )
    return Page[RegulationSummary](
        items=[RegulationSummary.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{code}", responses={404: {"description": "Regulation not found"}})
async def get_regulation(code: str, session: SessionDep) -> RegulationRead:
    try:
        regulation = await service.get_regulation(session, code)
    except service.RegulationNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return RegulationRead.model_validate(regulation)
