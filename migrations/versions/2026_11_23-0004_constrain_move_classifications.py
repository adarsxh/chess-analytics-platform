"""constrain move classification labels and losses

Classifier v1 (ADR-024) fixes the allowed labels, deferred in DATA_MODEL §4.3 until the
classifier was designed. Written by hand: autogenerate does not detect CHECK constraints,
so tests/integration/test_data_model_guarantees.py checks they exist.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23 10:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy: a migration must not change when the model's constant changes later.
LABELS = ("forced", "best", "excellent", "good", "inaccuracy", "mistake", "blunder")


def upgrade() -> None:
    allowed = ", ".join(f"'{label}'" for label in LABELS)
    op.create_check_constraint(
        op.f("ck_move_classifications_label"),
        "move_classifications",
        f"label IN ({allowed})",
        schema="analysis",
    )
    op.create_check_constraint(
        op.f("ck_move_classifications_eval_loss_non_negative"),
        "move_classifications",
        "eval_loss >= 0",
        schema="analysis",
    )


def downgrade() -> None:
    for name in ("ck_move_classifications_eval_loss_non_negative", "ck_move_classifications_label"):
        op.drop_constraint(op.f(name), "move_classifications", schema="analysis", type_="check")
