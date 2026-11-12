"""Shared SQLAlchemy metadata: database schemas, naming convention and declarative base.

Every table definition (added in later steps) uses `Base`, so all tables register on the
same `metadata`. Alembic compares that metadata with the real database to detect
missing migrations.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

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
