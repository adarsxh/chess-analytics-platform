"""`analysis` schema: engine results, versioned per engine config and classifier version.

See DATA_MODEL.md §4.3 and ADR-017. Analysis rows reference `core.games` with RESTRICT:
expensive engine results are never deleted as a side effect of deleting a game.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from chess_analytics.storage.schema import (
    ANALYSIS,
    Base,
    CreatedAtMixin,
    IdMixin,
    TimestampsMixin,
    one_of,
)


class AnalysisConfig(IdMixin, CreatedAtMixin, Base):
    """A distinct set of engine settings. Identical settings share one row (config_hash)."""

    __tablename__ = "analysis_configs"
    __table_args__ = (
        UniqueConstraint("config_hash"),
        CheckConstraint("nodes_per_position > 0", name="nodes_per_position_positive"),
        CheckConstraint("threads >= 1", name="threads_positive"),
        CheckConstraint("hash_mb >= 1", name="hash_mb_positive"),
        CheckConstraint("multipv >= 1", name="multipv_positive"),
        {"schema": ANALYSIS},
    )

    engine_name: Mapped[str]  # e.g. "stockfish"
    engine_version: Mapped[str]  # e.g. "17.1"
    nodes_per_position: Mapped[int] = mapped_column(BigInteger)
    threads: Mapped[int] = mapped_column(SmallInteger)
    hash_mb: Mapped[int]
    multipv: Mapped[int] = mapped_column(SmallInteger)
    config_hash: Mapped[str]  # hash of all settings above


class GameAnalysis(IdMixin, TimestampsMixin, Base):
    """One analysis of one game with one config. A new config adds a row; old ones stay."""

    __tablename__ = "game_analyses"
    __table_args__ = (
        UniqueConstraint("game_id", "analysis_config_id"),
        Index(None, "analysis_config_id"),  # "all games analysed with config X" (re-analysis)
        one_of("status", ("pending", "running", "succeeded", "failed")),
        {"schema": ANALYSIS},
    )

    game_id: Mapped[int] = mapped_column(ForeignKey("core.games.id", ondelete="RESTRICT"))
    analysis_config_id: Mapped[int] = mapped_column(
        ForeignKey("analysis.analysis_configs.id", ondelete="RESTRICT")
    )
    # Copied from core.games at analysis time; a mismatch later means this analysis is stale.
    moves_hash: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="pending")
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None]


class PositionEvaluation(Base):
    """Engine output for one position (ply 0 = start position). Scores from White's view."""

    __tablename__ = "position_evaluations"
    __table_args__ = (
        # A score is EITHER centipawns OR forced mate, never both, never neither.
        CheckConstraint(
            "(score_cp IS NULL) <> (mate_in IS NULL)", name="exactly_one_of_score_cp_mate_in"
        ),
        CheckConstraint("ply >= 0", name="ply_non_negative"),
        CheckConstraint("pv_rank >= 1", name="pv_rank_positive"),
        {"schema": ANALYSIS},
    )

    game_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis.game_analyses.id", ondelete="CASCADE"), primary_key=True
    )
    ply: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    pv_rank: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default="1")
    score_cp: Mapped[int | None]
    mate_in: Mapped[int | None] = mapped_column(SmallInteger)  # +n White mates, -n Black mates
    best_move_uci: Mapped[str | None]  # null in terminal positions
    pv_uci: Mapped[list[str] | None]  # first moves of the principal variation
    depth_reached: Mapped[int] = mapped_column(SmallInteger)
    nodes_searched: Mapped[int] = mapped_column(BigInteger)


class MoveClassification(Base):
    """Classifier output for one move; re-classifying adds rows under a new version."""

    __tablename__ = "move_classifications"
    __table_args__ = (
        CheckConstraint("ply >= 1", name="ply_positive"),
        CheckConstraint("classifier_version >= 1", name="classifier_version_positive"),
        {"schema": ANALYSIS},
    )

    game_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analysis.game_analyses.id", ondelete="CASCADE"), primary_key=True
    )
    ply: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    classifier_version: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str]  # allowed values are fixed when the classifier is designed
    eval_loss: Mapped[Decimal]  # the quantity the classifier used (cp or win-probability)
