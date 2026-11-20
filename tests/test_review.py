"""Multi-agent review tests: fake LLM by role + in-memory checkpointer. No Ollama needed."""

import uuid

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.api.cases import get_review_graph
from mizan.main import app
from mizan.review.graph import build_review_graph, clean_article_number, compute_risk
from mizan.review.outputs import (
    ArticleAssessment,
    CaseFacts,
    CheckerOutput,
    RecommendedAction,
    WriterOutput,
)
from tests.factories import make_demo_regulations
from tests.fakes import FakeStructuredModel

pytestmark = pytest.mark.anyio

RULES = {
    "cyber": {"code": "DEMO-SAMA-CSF", "articles": {"1.1": "Scope", "3.2": "Report in 24h"}},
    "data": {"code": "DEMO-PDPL", "articles": {"5": "Consent"}},
    "invoice": {"code": "DEMO-ZATCA-EINV", "articles": {"4": "QR code"}},
}
FACTS = CaseFacts(
    data_collected=["phone"],
    collection_purposes=["onboarding"],
    consent_described="single box",
    data_storage_locations=["Riyadh"],
    cross_border_assessment_done=None,
    board_approved_security_strategy=True,
    security_chief_reports_to="CEO",
    incident_reporting_deadline="72 hours",
    issues_tax_invoices=True,
    invoice_fields=["VAT number"],
    invoice_retention=None,
    other_relevant_facts=[],
)


def assess(number: str, status: str) -> ArticleAssessment:
    return ArticleAssessment(article_number=number, status=status, explanation=f"{number} why")


def fake_model(writer_replies: list[WriterOutput] | None = None) -> FakeStructuredModel:
    return FakeStructuredModel(
        {
            "document_reader": [FACTS],
            "cyber_checker": [
                CheckerOutput(
                    assessments=[
                        assess("Art. 3.2", "non_compliant"),  # prefix must be cleaned
                        assess("1.1", "compliant"),
                        assess("99", "compliant"),  # invented article: must be dropped
                    ]
                )
            ],
            "data_checker": [CheckerOutput(assessments=[assess("5", "compliant")])],
            "invoice_checker": [CheckerOutput(assessments=[])],  # skipped everything
            "report_writer": writer_replies
            or [
                WriterOutput(
                    summary="Draft summary.",
                    actions=[
                        RecommendedAction(
                            regulation_code="DEMO-SAMA-CSF",
                            article_number="3.2",
                            action="Report within 24 hours.",
                        )
                    ],
                )
            ],
        }
    )


async def start(llm: FakeStructuredModel) -> tuple[object, dict]:
    graph = build_review_graph(llm, InMemorySaver())  # type: ignore[arg-type]
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await graph.ainvoke({"title": "Test product", "documents": [], "rules": RULES}, config)
    return graph, config


async def test_review_runs_all_agents_and_pauses_for_the_human() -> None:
    llm = fake_model()
    graph, config = await start(llm)

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("human_review",)  # paused, waiting for the officer
    assert set(llm.calls) == {
        "document_reader",
        "cyber_checker",
        "data_checker",
        "invoice_checker",
        "report_writer",
    }
    report = snapshot.values["report"]
    by_article = {(f["regulation_code"], f["article_number"]): f for f in report["findings"]}
    # The reducer merged all three parallel checkers; one entry per real article.
    assert set(by_article) == {
        ("DEMO-SAMA-CSF", "1.1"),
        ("DEMO-SAMA-CSF", "3.2"),
        ("DEMO-PDPL", "5"),
        ("DEMO-ZATCA-EINV", "4"),
    }
    assert by_article[("DEMO-SAMA-CSF", "3.2")]["status"] == "non_compliant"  # "Art. 3.2" ok
    assert by_article[("DEMO-ZATCA-EINV", "4")]["status"] == "needs_info"  # skipped -> flagged
    assert report["risk_level"] == "high"  # computed by code from the findings
    assert report["revision"] == 1


async def test_every_problem_finding_gets_an_action_even_if_the_writer_drops_it() -> None:
    graph, config = await start(fake_model())

    actions = (await graph.aget_state(config)).values["report"]["recommended_actions"]

    assert actions == [
        "[DEMO-SAMA-CSF Art. 3.2] Report within 24 hours.",  # written by the LLM
        "[DEMO-ZATCA-EINV Art. 4] Provide the missing information: "
        "Not assessed by the checker; needs manual review.",  # filled in by code
    ]


async def test_officer_approval_ends_the_review() -> None:
    graph, config = await start(fake_model())

    await graph.ainvoke(Command(resume={"decision": "approve", "comment": None}), config)

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ()  # finished
    assert snapshot.values["decision"] == "approve"


async def test_returned_report_is_revised_with_the_comment_and_paused_again() -> None:
    llm = fake_model(
        writer_replies=[
            WriterOutput(summary="Draft one.", actions=[]),
            WriterOutput(summary="Draft two, as requested.", actions=[]),
        ]
    )
    graph, config = await start(llm)

    await graph.ainvoke(
        Command(resume={"decision": "return", "comment": "Lead with the 72-hour issue"}),
        config,
    )

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("human_review",)  # waiting for the officer again
    assert snapshot.values["report"]["summary"] == "Draft two, as requested."
    assert snapshot.values["report"]["revision"] == 2
    assert "Lead with the 72-hour issue" in str(llm.calls["report_writer"][1][-1].content)
    assert len(llm.calls["cyber_checker"]) == 1  # checks are NOT re-run, only the report


@pytest.mark.parametrize(
    ("raw", "clean"), [("Art. 3.2", "3.2"), ("Article 29", "29"), ("A5", "5"), ("12", "12")]
)
def test_clean_article_number(raw: str, clean: str) -> None:
    assert clean_article_number(raw) == clean


def test_risk_is_computed_from_findings() -> None:
    assert compute_risk([{"status": "compliant"}]) == "low"
    assert compute_risk([{"status": "compliant"}, {"status": "needs_info"}]) == "medium"
    assert compute_risk([{"status": "needs_info"}, {"status": "non_compliant"}]) == "high"


# ---------- through the API and the cases table ----------


async def test_case_lifecycle_through_the_api(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await make_demo_regulations(db_session)
    # ONE graph (and one checkpointer) shared by all requests, like the app's startup does.
    # A new checkpointer per request would forget the paused review between /review and
    # /decision - exactly what the Postgres checkpointer prevents in production.
    graph = build_review_graph(fake_model(), InMemorySaver())  # type: ignore[arg-type]
    app.dependency_overrides[get_review_graph] = lambda: graph
    created = await api.post(
        "/cases",
        json={"title": "Test product", "documents": [{"name": "Overview", "text": "A wallet."}]},
    )
    assert created.status_code == 201
    case_id = created.json()["id"]
    assert created.json()["status"] == "draft"

    # Deciding before any review is a state error, not a crash.
    early = await api.post(f"/cases/{case_id}/decision", json={"decision": "approve"})
    assert early.status_code == 409

    reviewed = await api.post(f"/cases/{case_id}/review")
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "awaiting_approval"
    assert reviewed.json()["report"]["risk_level"] == "high"

    no_comment = await api.post(f"/cases/{case_id}/decision", json={"decision": "return"})
    assert no_comment.status_code == 422  # returning requires a comment

    approved = await api.post(f"/cases/{case_id}/decision", json={"decision": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_at"] is not None


async def test_unknown_case_is_404(api: httpx.AsyncClient) -> None:
    response = await api.get(f"/cases/{uuid.uuid4()}")
    assert response.status_code == 404
