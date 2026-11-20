"""Compliance review cases: documents in, AI report out, human decision on top."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mizan.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class CaseStatus(StrEnum):
    DRAFT = "draft"  # created, not reviewed yet
    AWAITING_APPROVAL = "awaiting_approval"  # AI report ready; waiting for a compliance officer
    APPROVED = "approved"  # officer approved the report: final


class Case(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cases"

    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[CaseStatus] = mapped_column(
        str_enum(CaseStatus, "case_status"), default=CaseStatus.DRAFT
    )
    # JSONB: Postgres stores JSON in a binary, queryable form. Good for nested data whose
    # shape is defined by our Pydantic schemas rather than by table columns.
    documents: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)  # [{"name", "text"}, ...]
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # latest AI report
    revision: Mapped[int] = mapped_column(default=0)  # report versions (1 = first draft)
    reviewer_comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Case {self.title!r} {self.status}>"
