"""The multi-agent review graph.

                    +--> check_cyber   (SAMA-CSF) --+
    START --> read -+--> check_data    (PDPL)     --+--> write_report --> human_review --> END
                    +--> check_invoice (ZATCA)    --+         ^               |  (approve)
                         parallel "fan-out"                   +---------------+
                                                                (return + comment)

Design choices:
- FIXED orchestration: the graph decides who works when. An LLM "supervisor" choosing the
  next agent is more flexible but less predictable; a compliance review always needs every
  check, so there is nothing for a supervisor to decide.
- SPECIALISTS: each agent sees only what its job needs (one regulation, the facts), with
  short focused instructions. Small models do much better with narrow tasks.
- The graph is PURE: no database access. Regulation texts arrive in the input state, which
  makes the graph easy to test and safe to pause/resume.
"""

import json
import logging
import operator
import re
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from mizan.review.outputs import CaseFacts, CheckerOutput, WriterOutput

logger = logging.getLogger(__name__)

# Which regulation each checker is responsible for.
AREAS: dict[str, tuple[str, str]] = {
    "cyber": ("DEMO-SAMA-CSF", "cyber security"),
    "data": ("DEMO-PDPL", "personal data protection"),
    "invoice": ("DEMO-ZATCA-EINV", "e-invoicing"),
}


class Rules(TypedDict):
    code: str
    articles: dict[str, str]  # article_number -> "heading: body"


class ReviewState(TypedDict, total=False):
    """Everything the agents share. Saved by the checkpointer at every step."""

    title: str
    documents: list[dict[str, str]]
    rules: dict[str, Rules]  # area -> its regulation's articles
    facts: dict[str, Any]
    # REDUCER: the three checkers run in parallel and all write `findings`. operator.add
    # tells LangGraph to CONCATENATE their lists instead of letting the last one overwrite.
    findings: Annotated[list[dict[str, str]], operator.add]
    report: dict[str, Any]
    decision: str
    reviewer_comment: str | None


async def ask[T: BaseModel](
    llm: BaseChatModel, schema: type[T], role: str, system: str, user: str
) -> T:
    """One structured LLM call. `role` is the first line of the system prompt."""
    result = await llm.with_structured_output(schema).ainvoke(
        [SystemMessage(f"ROLE: {role}\n{system}"), HumanMessage(user)]
    )
    if not isinstance(result, schema):  # validated by Pydantic; anything else is a bug
        raise TypeError(f"{role} returned {type(result).__name__}, expected {schema.__name__}")
    return result


_ARTICLE_PREFIX = re.compile(r"^\s*(article|art\.?|a)\s*", re.IGNORECASE)


def clean_article_number(raw: str) -> str:
    """Models write "Art. 3.2", "Article 3.2" or "A5" for "3.2" / "5". Normalise, then match."""
    return _ARTICLE_PREFIX.sub("", raw).strip().rstrip(".:")


def complete_actions(findings: list[dict[str, str]], output: WriterOutput) -> list[str]:
    """One action per problem finding, GUARANTEED by code.

    Seen in testing: asked to "name the three most urgent issues", the writer returned only
    3 of 7 actions. An officer approving that report would miss 4 required fixes. So the
    writer's actions are matched to findings, and any finding left without one gets a
    fallback action built from the checker's explanation.
    """
    written = {
        (a.regulation_code, clean_article_number(a.article_number)): a.action
        for a in output.actions
    }
    actions = []
    for finding in findings:
        if finding["status"] == "compliant":
            continue
        key = (finding["regulation_code"], finding["article_number"])
        action = written.get(key) or (
            f"Review and fix: {finding['explanation']}"
            if finding["status"] == "non_compliant"
            else f"Provide the missing information: {finding['explanation']}"
        )
        actions.append(f"[{key[0]} Art. {key[1]}] {action}")
    return actions


def compute_risk(findings: list[dict[str, str]]) -> Literal["low", "medium", "high"]:
    """Deterministic: the same findings always give the same risk. Auditors can check it."""
    statuses = {finding["status"] for finding in findings}
    if "non_compliant" in statuses:
        return "high"
    if "needs_info" in statuses:
        return "medium"
    return "low"


def build_review_graph(llm: BaseChatModel, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    # ---------- agent 1: document reader ----------
    async def read_documents(state: ReviewState) -> ReviewState:
        documents = "\n\n".join(f"## {d['name']}\n{d['text']}" for d in state["documents"])
        facts = await ask(
            llm,
            CaseFacts,
            role="document_reader",
            system=(
                "You extract facts from a bank's product documents for a compliance review.\n"
                "Report ONLY what the documents state; copy wording where asked (verbatim).\n"
                "Use null or an empty list when the documents do not say. Never infer or guess."
            ),
            user=f"Product: {state['title']}\n\n{documents}",
        )
        return {"facts": facts.model_dump()}

    # ---------- agents 2-4: one compliance checker per regulation ----------
    def make_checker(area: str):  # noqa: ANN202 - returns a LangGraph node function
        code, topic = AREAS[area]

        async def check(state: ReviewState) -> ReviewState:
            rules = state["rules"][area]
            articles = "\n".join(
                f'- article_number "{num}": {text}' for num, text in rules["articles"].items()
            )
            output = await ask(
                llm,
                CheckerOutput,
                role=f"{area}_checker",
                system=(
                    f"You are the {topic} compliance checker for {code}.\n"
                    "Assess the product against EACH article below, one assessment per article:\n"
                    "- compliant: the facts show the article is met\n"
                    "- non_compliant: the facts show it is NOT met\n"
                    "- needs_info: the facts do not say enough to decide\n"
                    "Base every assessment only on the FACTS. Do not assume anything."
                ),
                user=f"ARTICLES of {code}:\n{articles}\n\nFACTS (JSON):\n"
                + json.dumps(state["facts"], ensure_ascii=False, indent=1),
            )
            findings = []
            for item in output.assessments:
                number = clean_article_number(item.article_number)
                # Guardrail: only articles that really exist in this regulation, once each.
                if number not in rules["articles"] or number in {
                    f["article_number"] for f in findings
                }:
                    logger.warning("%s: unknown/duplicate article %r dropped", area, number)
                    continue
                findings.append(
                    {
                        "regulation_code": code,
                        "article_number": number,
                        "status": item.status,
                        "explanation": item.explanation,
                    }
                )
            # Any article the model skipped is recorded as needs_info, never silently missing.
            assessed = {f["article_number"] for f in findings}
            for number in rules["articles"]:
                if number not in assessed:
                    findings.append(
                        {
                            "regulation_code": code,
                            "article_number": number,
                            "status": "needs_info",
                            "explanation": "Not assessed by the checker; needs manual review.",
                        }
                    )
            return {"findings": findings}

        return check

    # ---------- agent 5: report writer ----------
    async def write_report(state: ReviewState) -> ReviewState:
        findings = state["findings"]
        revision = state.get("report", {}).get("revision", 0) + 1
        comment = state.get("reviewer_comment") if state.get("decision") == "return" else None
        feedback = (
            f"\n\nThe compliance officer RETURNED the previous draft with this comment. "
            f"Address it:\n{comment}"
            if comment
            else ""
        )
        output = await ask(
            llm,
            WriterOutput,
            role="report_writer",
            system=(
                "You write the summary of a compliance review for a bank's compliance officer.\n"
                "Use only the findings given. Be specific and brief. Cite articles as "
                "[CODE Art. N]. Give one concrete action for EVERY non_compliant or needs_info "
                "finding, even when the summary focuses on the most urgent ones."
            ),
            user=f"Product: {state['title']}\n\nFINDINGS (JSON):\n"
            + json.dumps(findings, ensure_ascii=False, indent=1)
            + feedback,
        )
        report = {
            "risk_level": compute_risk(findings),
            "summary": output.summary,
            "recommended_actions": complete_actions(findings, output),
            "findings": findings,
            "revision": revision,
        }
        return {"report": report}

    # ---------- the human: compliance officer ----------
    async def human_review(state: ReviewState) -> ReviewState:
        # interrupt(): SAVE the whole state (checkpointer) and STOP here. The graph resumes
        # only when someone calls it again with Command(resume=...) - minutes or days later,
        # even after a server restart. The resume value is what interrupt() returns.
        decision = interrupt({"report": state["report"]})
        return {"decision": decision["decision"], "reviewer_comment": decision.get("comment")}

    def after_human_review(state: ReviewState) -> str:
        return END if state["decision"] == "approve" else "write_report"

    graph = StateGraph(ReviewState)
    graph.add_node("read_documents", read_documents)
    for area in AREAS:
        graph.add_node(f"check_{area}", make_checker(area))
    graph.add_node("write_report", write_report)
    graph.add_node("human_review", human_review)

    graph.add_edge(START, "read_documents")
    for area in AREAS:
        graph.add_edge("read_documents", f"check_{area}")  # fan-out: all three start together
    # fan-in: write_report waits until ALL three checkers have finished.
    graph.add_edge([f"check_{area}" for area in AREAS], "write_report")
    graph.add_edge("write_report", "human_review")
    graph.add_conditional_edges("human_review", after_human_review, ["write_report", END])

    # The checkpointer is what makes pausing possible: state is saved after every step.
    return graph.compile(checkpointer=checkpointer)
