"""Regulations and their articles: the knowledge base Mizan answers from.

One regulation (e.g. a SAMA framework) has many articles. Answers cite articles, so an
article is the smallest unit we can point to: "SAMA-CSF, Article 3.2.1".
In week 3 each article is split into chunks with embeddings for RAG.
"""

import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mizan.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class Authority(StrEnum):
    SAMA = "SAMA"  # Saudi Central Bank
    ZATCA = "ZATCA"  # Zakat, Tax and Customs Authority
    SDAIA = "SDAIA"  # Data & AI Authority (Personal Data Protection Law, PDPL)
    CMA = "CMA"  # Capital Market Authority


class RegulationStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"  # replaced by a newer version; kept for audit history


class Language(StrEnum):
    EN = "en"
    AR = "ar"


class Regulation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "regulations"

    # Short, stable reference used in citations, e.g. "SAMA-CSF". unique=True adds an index too.
    code: Mapped[str] = mapped_column(String(50), unique=True)
    authority: Mapped[Authority] = mapped_column(str_enum(Authority, "authority"))
    title_en: Mapped[str] = mapped_column(String(300))
    # `str | None` -> the column is NULLable. Plain `str` -> NOT NULL. The type hint decides.
    title_ar: Mapped[str | None] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(20), default="1.0")
    status: Mapped[RegulationStatus] = mapped_column(
        str_enum(RegulationStatus, "regulation_status"), default=RegulationStatus.ACTIVE
    )
    effective_date: Mapped[date | None]
    source_url: Mapped[str | None] = mapped_column(String(500))

    # Not a column: a Python-side link. regulation.articles loads the related rows.
    # cascade="all, delete-orphan": deleting a regulation (or removing an article from the
    # list) deletes those articles too.
    articles: Mapped[list["Article"]] = relationship(
        back_populates="regulation",
        cascade="all, delete-orphan",
        order_by="Article.position",
    )

    def __repr__(self) -> str:
        return f"<Regulation {self.code} v{self.version}>"


class Article(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "articles"
    # The same article number can exist once per language (English + Arabic text of 3.2.1).
    __table_args__ = (UniqueConstraint("regulation_id", "article_number", "language"),)

    # ondelete="CASCADE": the database itself removes articles if the regulation row is deleted.
    # index=True: "all articles of regulation X" is our most common query.
    regulation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("regulations.id", ondelete="CASCADE"), index=True
    )
    # A string, not an int: real numbering looks like "3.2.1" or "12-bis".
    article_number: Mapped[str] = mapped_column(String(30))
    language: Mapped[Language] = mapped_column(str_enum(Language, "language"))
    heading: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    # Reading order inside the regulation (article numbers don't sort correctly as text).
    position: Mapped[int]

    regulation: Mapped[Regulation] = relationship(back_populates="articles")

    def __repr__(self) -> str:
        return f"<Article {self.article_number} ({self.language})>"
