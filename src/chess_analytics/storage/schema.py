"""Shared SQLAlchemy metadata: database schemas, naming convention and declarative base.

Every table definition (added in later steps) uses `Base`, so all tables register on the
same `metadata`. Alembic compares that metadata with the real database to detect
missing migrations.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, MetaData, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Postgres schemas, one per logical data layer (ADR-019).
RAW = "raw"
CORE = "core"
ANALYSIS = "analysis"
OPS = "ops"
SCHEMAS: tuple[str, ...] = (RAW, CORE, ANALYSIS, OPS)

# Deterministic constraint and index names. Without this, Postgres invents names and
# migrations cannot reliably drop or alter constraints later. Postgres truncates names
# longer than 63 characters, so keep table and column names reasonably short.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata
    # Default SQL types for Python annotations (ADR-019): text not varchar, timestamptz
    # not naive timestamps, jsonb not json.
    type_annotation_map = {
        str: Text,
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


# ------------------------------------------------------------ reusable parts ---


class IdMixin:
    """Surrogate primary key: bigint GENERATED ALWAYS AS IDENTITY (ADR-014)."""

    # sort_order: Postgres column order is permanent, so put id first and audit columns last.
    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True, sort_order=-100
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)


class TimestampsMixin(CreatedAtMixin):
    # onupdate is applied by SQLAlchemy when it issues the UPDATE (not by a DB trigger).
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=101
    )


def one_of(column: str, values: Iterable[str]) -> CheckConstraint:
    """`CHECK (column IN (...))`, our replacement for native ENUM types (ADR-019).

    Named after the column, so the naming convention yields e.g. `ck_games_result`.
    """
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=column)


# Allowed values shared across tables.
PLATFORMS = ("chesscom",)
