"""Pydantic schemas: the shape of data entering and leaving the API.

Models (mizan.models) describe TABLES. Schemas describe the API CONTRACT. Keeping them separate
means we can change a table without breaking clients, and never leak internal columns.

  *Create  -> request body (validated input)       ~ Laravel Form Request
  *Read    -> response body (what clients receive)  ~ Laravel API Resource
"""

import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

from mizan.models import Authority, Language, RegulationStatus

# Reusable constrained types. `Annotated[type, rules...]` attaches validation to a type.


def _normalise_code(value: object) -> object:
    # BeforeValidator runs BEFORE the type/pattern checks. StringConstraints(to_upper=True)
    # would upper-case only AFTER checking the pattern, so " sama-csf " would be rejected.
    return value.strip().upper() if isinstance(value, str) else value


# Codes look like "SAMA-CSF": letters/digits/hyphens, 3-50 chars, any case or spacing accepted.
RegulationCode = Annotated[
    str,
    BeforeValidator(_normalise_code),
    StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9-]{1,48}[A-Z0-9]$"),
]
# Validated as a real URL; PlainSerializer turns it into a plain string in model_dump(),
# which is what the database column stores.
Url = Annotated[HttpUrl, PlainSerializer(str, return_type=str)]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# ---------- input ----------


class ArticleCreate(BaseModel):
    article_number: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)
    ]
    language: Language
    heading: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    body: NonEmptyText
    position: int = Field(ge=0)


class RegulationCreate(BaseModel):
    # extra="forbid": unknown fields (e.g. a typo "titel_en", or an attacker sending "id") -> 422.
    model_config = ConfigDict(extra="forbid")

    code: RegulationCode
    authority: Authority
    title_en: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    title_ar: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    version: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = "1.0"
    status: RegulationStatus = RegulationStatus.ACTIVE
    effective_date: date | None = None
    source_url: Url | None = None
    articles: list[ArticleCreate] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def articles_are_unique(self) -> "RegulationCreate":
        # Catch duplicates here (clear 422 message) instead of a database error later.
        seen: set[tuple[str, Language]] = set()
        for article in self.articles:
            key = (article.article_number, article.language)
            if key in seen:
                raise ValueError(
                    f"Duplicate article {article.article_number} ({article.language.value})"
                )
            seen.add(key)
        return self


# ---------- output ----------


class ArticleRead(BaseModel):
    # from_attributes: build this schema from an ORM object (article.body) instead of a dict.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    article_number: str
    language: Language
    heading: str | None
    body: str
    position: int


class RegulationSummary(BaseModel):
    """Light version for lists: no article text, so a page of 20 stays small and fast."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    authority: Authority
    title_en: str
    title_ar: str | None
    version: str
    status: RegulationStatus
    effective_date: date | None
    created_at: datetime


class RegulationRead(RegulationSummary):
    """Full version for one regulation: everything in the summary plus its articles."""

    source_url: str | None
    updated_at: datetime
    articles: list[ArticleRead]


class Page[T](BaseModel):
    """Generic paginated response: Page[RegulationSummary], later Page[Case], Page[...]."""

    items: list[T]
    total: int
    limit: int
    offset: int
