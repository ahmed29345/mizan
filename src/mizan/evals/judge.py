"""LLM-as-judge: a second LLM call that checks if the answer is SUPPORTED by its sources.

String checks (metrics.py) can't tell "within 24 hours" from "within 24 days, see Art. 3.2"
when both contain "24". A judge reads the answer and the cited article texts and decides.
Caveats: the judge is also an LLM and can be wrong; a judge from the same model family tends
to be lenient with itself. Spot-check its verdicts by hand, and prefer a stronger judge model.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

JUDGE_PROMPT = """\
You are a strict auditor checking an AI assistant's answer about regulations.
You get the QUESTION, the ANSWER, and the SOURCES (the full text of the articles it cited).
Decide if EVERY factual claim in the ANSWER is directly supported by the SOURCES.
Numbers, deadlines, durations and who-must-do-what must match exactly.
Ignore style and politeness. Do not use any knowledge outside the SOURCES."""


class GroundednessVerdict(BaseModel):
    """Structured output: the judge must reply in exactly this shape (validated by Pydantic)."""

    unsupported_claims: list[str] = Field(
        description="Each claim in the answer that the sources do not support. Empty if none."
    )
    grounded: bool = Field(description="True only if unsupported_claims is empty.")


async def judge_groundedness(
    judge: BaseChatModel, *, question: str, answer: str, sources: str
) -> GroundednessVerdict:
    structured = judge.with_structured_output(GroundednessVerdict)
    verdict = await structured.ainvoke(
        [
            SystemMessage(JUDGE_PROMPT),
            HumanMessage(f"QUESTION:\n{question}\n\nANSWER:\n{answer}\n\nSOURCES:\n{sources}"),
        ]
    )
    assert isinstance(verdict, GroundednessVerdict)
    return verdict
