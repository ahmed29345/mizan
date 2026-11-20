"""API shapes for compliance review cases."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mizan.models import CaseStatus


class CaseDocument(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=50_000)


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=200)
    documents: list[CaseDocument] = Field(min_length=1, max_length=20)


class FindingStatus(StrEnum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    NEEDS_INFO = "needs_info"  # the documents don't say enough to decide


class Finding(BaseModel):
    regulation_code: str
    article_number: str
    status: FindingStatus
    explanation: str


class CaseReport(BaseModel):
    # Computed by CODE from the findings, never chosen by an LLM (see review/graph.py).
    risk_level: Literal["low", "medium", "high"]
    summary: str
    recommended_actions: list[str]
    findings: list[Finding]
    revision: int


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: CaseStatus
    documents: list[CaseDocument]
    report: CaseReport | None
    revision: int
    reviewer_comment: str | None
    decided_at: datetime | None
    created_at: datetime


class DecisionRequest(BaseModel):
    """The compliance officer's decision on the AI report."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "return"]
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def return_needs_a_comment(self) -> "DecisionRequest":
        # Sending a report back without saying what's wrong gives the writer nothing to fix.
        if self.decision == "return" and not (self.comment and self.comment.strip()):
            raise ValueError("A comment is required when returning a report")
        return self


class CaseSummary(BaseModel):
    """One row in the case list (no documents or full report)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: CaseStatus
    revision: int
    created_at: datetime
    risk_level: str | None = None


class CaseTemplate(BaseModel):
    """A ready-made demo case (data/seed/cases/*.json), with the findings an expert expects."""

    id: str
    title: str
    documents: list[CaseDocument]
    expected: dict[str, str] = {}
