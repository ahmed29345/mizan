"""What each agent must return: structured output schemas.

The LLM is forced to reply in these shapes (JSON matching the schema) and Pydantic validates
the reply. Field descriptions are sent to the model, so they are part of the prompt.
"""

from typing import Literal

from pydantic import BaseModel, Field


class CaseFacts(BaseModel):
    """Document reader output: facts stated in the documents. null = not stated."""

    data_collected: list[str] = Field(description="Personal data fields collected from customers")
    collection_purposes: list[str] = Field(description="Stated purposes for collecting the data")
    consent_described: str | None = Field(description="How customer consent is obtained, verbatim")
    data_storage_locations: list[str] = Field(description="Countries/cities where data is stored")
    cross_border_assessment_done: bool | None = Field(
        description="Whether a cross-border transfer assessment is stated as done"
    )
    board_approved_security_strategy: bool | None
    security_chief_reports_to: str | None = Field(
        description="Who the information security chief / CISO reports to"
    )
    incident_reporting_deadline: str | None = Field(
        description="Deadline for reporting cyber incidents to the regulator, verbatim"
    )
    issues_tax_invoices: bool | None
    invoice_fields: list[str] = Field(description="Fields shown on the tax invoices")
    invoice_retention: str | None = Field(description="How long and how invoices are kept")
    other_relevant_facts: list[str] = Field(description="Anything else relevant to compliance")


class ArticleAssessment(BaseModel):
    article_number: str = Field(description='Only the number, exactly as given, e.g. "3.2"')
    status: Literal["compliant", "non_compliant", "needs_info"]
    explanation: str = Field(description="One sentence: which fact leads to this status")


class CheckerOutput(BaseModel):
    """Compliance checker output: one assessment per article of its regulation."""

    assessments: list[ArticleAssessment]


class RecommendedAction(BaseModel):
    regulation_code: str
    article_number: str = Field(description='Only the number, e.g. "3.2"')
    action: str = Field(description="One concrete action that fixes this finding")


class WriterOutput(BaseModel):
    """Report writer output. The risk level is NOT here: code computes it."""

    summary: str = Field(description="2-4 complete sentences for a compliance officer")
    actions: list[RecommendedAction] = Field(
        description="One action for EVERY non_compliant or needs_info finding"
    )
