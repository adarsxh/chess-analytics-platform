"""allow the analyse_player job type and the cancelled job status

Phase 9 (ADR-028): "Analyse all" queues one `analyse_player` job that expands into an
`analyse_game` job per unanalysed game; "Stop" marks the still-queued ones `cancelled`.
Written by hand: autogenerate does not detect CHECK constraint changes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-28 10:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies: a migration must not change when the model's constants change later.
OLD = {
    "job_type": ("sync_player", "analyse_game"),
    "status": ("queued", "running", "succeeded", "failed"),
}
NEW = {
    "job_type": ("sync_player", "analyse_game", "analyse_player"),
    "status": ("queued", "running", "succeeded", "failed", "cancelled"),
}


def _replace(values: dict[str, tuple[str, ...]]) -> None:
    for column, allowed in values.items():
        name = op.f(f"ck_jobs_{column}")
        op.drop_constraint(name, "jobs", schema="ops", type_="check")
        listed = ", ".join(f"'{value}'" for value in allowed)
        op.create_check_constraint(name, "jobs", f"{column} IN ({listed})", schema="ops")


def upgrade() -> None:
    _replace(NEW)


def downgrade() -> None:
    # Rows using the new values would violate the old constraints: remove them first.
    op.execute("DELETE FROM ops.jobs WHERE job_type = 'analyse_player' OR status = 'cancelled'")
    _replace(OLD)
