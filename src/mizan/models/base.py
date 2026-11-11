"""Shared building blocks for every database model."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable constraint names (e.g. "fk_articles_regulation_id_regulations").
# Without this, Postgres invents names and Alembic can't reliably drop/alter them later.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Every model inherits from this. `Base.metadata` is the list of all tables."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    # UUIDs instead of 1, 2, 3: safe to expose in URLs (not guessable) and can be created
    # anywhere (app, workers, other services) without asking the database first.
    # sort_order: mixin columns normally go last; this puts `id` first in the table.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, sort_order=-10)


class TimestampMixin:
    # server_default=func.now(): Postgres fills the time, so it's correct even for raw SQL inserts.
    # timezone=True -> TIMESTAMPTZ, stored in UTC; never store local times in a multi-region system.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), sort_order=10
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), sort_order=10
    )


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Store a Python StrEnum as VARCHAR + CHECK constraint, using its values ("active").

    native_enum=False avoids Postgres ENUM types, which are painful to change in migrations
    (adding a value needs special handling). A CHECK constraint still blocks bad values.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=30,
        values_callable=lambda e: [member.value for member in e],
    )
