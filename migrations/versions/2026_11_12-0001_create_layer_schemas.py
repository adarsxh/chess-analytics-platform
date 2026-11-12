"""create layer schemas

Creates one Postgres schema per logical data layer (ADR-019). Tables are added by later
migrations.

Revision ID: 0001
Revises:
Create Date: 2026-11-12 20:44:15.652570+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Hard-coded on purpose: a migration is a snapshot of history and must not change when
# application constants change later.
SCHEMAS = ("raw", "core", "analysis", "ops")


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA {schema}")


def downgrade() -> None:
    # RESTRICT (the default): fails if a schema still contains objects, so a downgrade can
    # never silently drop tables that a later migration created.
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA {schema}")
