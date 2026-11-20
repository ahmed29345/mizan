"""Request/response shapes for POST /ask (the agent)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=1000)


class Citation(BaseModel):
    regulation_code: str
    article_number: str
    # True only if a tool actually returned this article during the run.
    # False = the model cited something it never saw: a hallucinated citation.
    # True does NOT prove the article supports the claim (that needs evaluation, week 6).
    verified: bool


class ToolCallLog(BaseModel):
    tool: str
    args: dict[str, Any]


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[Citation]
    tool_calls: list[ToolCallLog]  # what the agent did, in order (transparency / audit)
    # Set when a guardrail replaced the answer with a safe refusal, e.g.
    # "input:prompt_injection", "output:ungrounded_numbers". None = answer passed all checks.
    blocked_by: str | None = None
    model: str
    llm_turns: int
    duration_ms: int
