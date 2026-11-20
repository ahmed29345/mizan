"""Case workflow: create a case, run the AI review, record the officer's decision.

The graph owns the REVIEW PROCESS (and its paused state in the checkpointer).
The `cases` table owns the BUSINESS RECORD (status, latest report, decision) for listing,
searching and audit. After every graph run we copy the graph's result into the table.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.models import Case, CaseStatus, Language
from mizan.review.graph import AREAS, Rules
from mizan.schemas.case import CaseCreate, DecisionRequest
from mizan.services.regulations import get_regulation


class CaseNotFoundError(Exception):
    pass


class InvalidCaseStateError(Exception):
    """E.g. approving a case that is not waiting for approval."""


def _graph_config(case: Case) -> dict[str, Any]:
    # thread_id links this case to its saved graph state in the checkpointer.
    return {"configurable": {"thread_id": str(case.id)}}


async def create_case(session: AsyncSession, data: CaseCreate) -> Case:
    case = Case(title=data.title, documents=[d.model_dump() for d in data.documents])
    session.add(case)
    await session.commit()
    return case


async def get_case(session: AsyncSession, case_id: uuid.UUID) -> Case:
    case = await session.get(Case, case_id)
    if case is None:
        raise CaseNotFoundError(str(case_id))
    return case


async def load_rules(session: AsyncSession) -> dict[str, Rules]:
    """The English articles of each checker's regulation, passed INTO the graph."""
    rules: dict[str, Rules] = {}
    for area, (code, _topic) in AREAS.items():
        regulation = await get_regulation(session, code)
        rules[area] = {
            "code": code,
            "articles": {
                a.article_number: f"{a.heading or ''}: {a.body}"
                for a in regulation.articles
                if a.language == Language.EN
            },
        }
    return rules


async def _sync_from_graph(session: AsyncSession, case: Case, graph: CompiledStateGraph) -> Case:
    snapshot = await graph.aget_state(_graph_config(case))
    values = snapshot.values
    case.report = values.get("report")
    case.revision = (case.report or {}).get("revision", 0)
    case.reviewer_comment = values.get("reviewer_comment")
    if snapshot.next:  # the graph has a next step pending = it is paused at human_review
        case.status = CaseStatus.AWAITING_APPROVAL
    else:  # the graph reached END = approved
        case.status = CaseStatus.APPROVED
        case.decided_at = datetime.now(UTC)
    await session.commit()
    return case


async def start_review(
    session: AsyncSession, graph: CompiledStateGraph, case_id: uuid.UUID
) -> Case:
    case = await get_case(session, case_id)
    if case.status != CaseStatus.DRAFT:
        raise InvalidCaseStateError(f"Case is {case.status}; only draft cases can be reviewed")
    # Runs reader -> 3 checkers -> writer, then STOPS at human_review (interrupt).
    await graph.ainvoke(
        {"title": case.title, "documents": case.documents, "rules": await load_rules(session)},
        _graph_config(case),
    )
    return await _sync_from_graph(session, case, graph)


async def decide(
    session: AsyncSession,
    graph: CompiledStateGraph,
    case_id: uuid.UUID,
    decision: DecisionRequest,
) -> Case:
    case = await get_case(session, case_id)
    if case.status != CaseStatus.AWAITING_APPROVAL:
        raise InvalidCaseStateError(f"Case is {case.status}; nothing is waiting for a decision")
    # Command(resume=...) continues the paused graph; interrupt() returns this value.
    # approve -> END.  return -> the writer revises with the comment -> pauses again.
    await graph.ainvoke(Command(resume=decision.model_dump()), _graph_config(case))
    return await _sync_from_graph(session, case, graph)
