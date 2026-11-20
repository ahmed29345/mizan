"""Case workflow: create a case, run the AI review, record the officer's decision.

The graph owns the REVIEW PROCESS (and its paused state in the checkpointer).
The `cases` table owns the BUSINESS RECORD (status, latest report, decision) for listing,
searching and audit. After every graph run we copy the graph's result into the table.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.models import Case, CaseStatus, Language
from mizan.observability import Trace, trace
from mizan.review.graph import AREAS, Rules
from mizan.schemas.case import CaseCreate, CaseTemplate, DecisionRequest
from mizan.services.regulations import get_regulation


class CaseNotFoundError(Exception):
    pass


class InvalidCaseStateError(Exception):
    """E.g. approving a case that is not waiting for approval."""


def _graph_config(case: Case, run: Trace) -> dict[str, Any]:
    # thread_id links this case to its saved graph state in the checkpointer;
    # callbacks record every agent's LLM call in the trace.
    return {"configurable": {"thread_id": str(case.id)}, "callbacks": run.callbacks}


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


async def list_cases(session: AsyncSession, limit: int = 50) -> list[Case]:
    rows = await session.scalars(select(Case).order_by(Case.created_at.desc()).limit(limit))
    return list(rows)


def load_templates(directory: Path) -> list[CaseTemplate]:
    """Demo cases from JSON files. Keys starting with "_" are notes, except _expected."""
    templates = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        templates.append(
            CaseTemplate(
                id=path.stem,
                title=raw["title"],
                documents=raw["documents"],
                expected=raw.get("_expected", {}),
            )
        )
    return templates


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
    snapshot = await graph.aget_state({"configurable": {"thread_id": str(case.id)}})
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
    rules = await load_rules(session)
    # session_id = case id: the review, any returns and the approval appear together in
    # Langfuse as one session, even though they happen in separate requests (or days).
    with trace(
        "case_review",
        as_type="agent",
        input={"title": case.title},
        session_id=str(case.id),
        tags=["review"],
    ) as run:
        # Runs reader -> 3 checkers -> writer, then STOPS at human_review (interrupt).
        await graph.ainvoke(
            {"title": case.title, "documents": case.documents, "rules": rules},
            _graph_config(case, run),
        )
        case = await _sync_from_graph(session, case, graph)
        run.update(output={"status": case.status, "risk": (case.report or {}).get("risk_level")})
    return case


async def decide(
    session: AsyncSession,
    graph: CompiledStateGraph,
    case_id: uuid.UUID,
    decision: DecisionRequest,
) -> Case:
    case = await get_case(session, case_id)
    if case.status != CaseStatus.AWAITING_APPROVAL:
        raise InvalidCaseStateError(f"Case is {case.status}; nothing is waiting for a decision")
    with trace(
        f"case_{decision.decision}",
        as_type="agent",
        input=decision.model_dump(),
        session_id=str(case.id),
        tags=["review", "human-decision"],
    ) as run:
        # Command(resume=...) continues the paused graph; interrupt() returns this value.
        # approve -> END.  return -> the writer revises with the comment -> pauses again.
        await graph.ainvoke(Command(resume=decision.model_dump()), _graph_config(case, run))
        case = await _sync_from_graph(session, case, graph)
        run.update(output={"status": case.status, "revision": case.revision})
    return case
