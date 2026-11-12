"""`core` schema: canonical, platform-neutral model; current state, rebuildable from `raw`.

See DATA_MODEL.md §4.2 and ADR-014 / ADR-016.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from chess_analytics.storage.schema import (
    CORE,
    PLATFORMS,
    Base,
    IdMixin,
    TimestampsMixin,
    one_of,
)

COLORS = ("white", "black")


class Player(IdMixin, TimestampsMixin, Base):
    """A chess identity on a platform (not an application user). Opponents included."""

    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("platform", "platform_uuid"),
        one_of("platform", PLATFORMS),
        # Case-insensitive lookup by username. Expression indexes need an explicit name.
        Index("ix_players_platform_lower_username", "platform", text("lower(username)")),
        # At most one "uuid not known yet" row per username, so two concurrent lookups of the
        # same new username can't create two placeholder players.
        Index(
            "uq_players_platform_lower_username_no_uuid",
            "platform",
            text("lower(username)"),
            unique=True,
            postgresql_where=text("platform_uuid IS NULL"),
        ),
        {"schema": CORE},
    )

    platform: Mapped[str]
    platform_uuid: Mapped[str | None]  # stable identity; learned from the first game seen
    platform_player_id: Mapped[int | None] = mapped_column(BigInteger)  # profile's numeric id
    username: Mapped[str]  # mutable, stored as received


class TrackedPlayer(Base):
    """Players we sync. Becomes "user follows player" in V4 (ADR-007)."""

    __tablename__ = "tracked_players"
    __table_args__ = ({"schema": CORE},)

    player_id: Mapped[int] = mapped_column(
        ForeignKey("core.players.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_synced_at: Mapped[datetime | None]


class Game(IdMixin, TimestampsMixin, Base):
    """Game-level facts. Player-specific facts live in GamePlayer."""

    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("platform", "platform_game_id"),
        one_of("platform", PLATFORMS),
        one_of("time_class", ("bullet", "blitz", "rapid", "daily")),
        one_of("result", ("white_win", "black_win", "draw")),
        CheckConstraint("ply_count >= 0", name="ply_count_non_negative"),
        CheckConstraint("base_seconds >= 0", name="base_seconds_non_negative"),
        CheckConstraint("increment_seconds >= 0", name="increment_seconds_non_negative"),
        # Postgres does not index FK columns automatically. "Needs processing" joins on this.
        Index(None, "raw_game_id"),
        {"schema": CORE},
    )

    platform: Mapped[str]
    platform_game_id: Mapped[str]
    url: Mapped[str]
    # Lineage: exactly which raw version and parser produced this row.
    raw_game_id: Mapped[int] = mapped_column(ForeignKey("raw.raw_games.id", ondelete="RESTRICT"))
    parser_version: Mapped[int]
    rules: Mapped[str]
    time_class: Mapped[str]
    time_control: Mapped[str]  # as received: "600", "180+2", "1/86400"
    base_seconds: Mapped[int | None]
    increment_seconds: Mapped[int | None]
    rated: Mapped[bool]
    start_time: Mapped[datetime | None]
    end_time: Mapped[datetime]
    result: Mapped[str]
    termination: Mapped[str | None]
    initial_fen: Mapped[str]
    final_fen: Mapped[str]
    ply_count: Mapped[int]
    moves_hash: Mapped[str]  # detects stale analyses (ADR-017)
    source_eco: Mapped[str | None]
    source_opening_name: Mapped[str | None]
    processed_at: Mapped[datetime] = mapped_column(server_default=func.now())


class GamePlayer(Base):
    """One row per side of a game (ADR-016): makes player-centric queries simple."""

    __tablename__ = "game_players"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id"),
        Index(None, "player_id", "game_id"),
        one_of("color", COLORS),
        one_of("outcome", ("win", "loss", "draw")),
        CheckConstraint(
            "source_accuracy BETWEEN 0 AND 100", name="source_accuracy_between_0_and_100"
        ),
        {"schema": CORE},
    )

    game_id: Mapped[int] = mapped_column(
        ForeignKey("core.games.id", ondelete="CASCADE"), primary_key=True
    )
    color: Mapped[str] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("core.players.id", ondelete="RESTRICT"))
    rating_after: Mapped[int | None]  # Chess.com rating is post-game (verified on real data)
    result_code: Mapped[str]  # source code as received, e.g. "timevsinsufficient"
    outcome: Mapped[str]
    source_accuracy: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))


class GameMove(Base):
    """One row per ply."""

    __tablename__ = "game_moves"
    __table_args__ = (
        CheckConstraint("ply >= 1", name="ply_positive"),
        CheckConstraint("clock_ms >= 0", name="clock_ms_non_negative"),
        {"schema": CORE},
    )

    game_id: Mapped[int] = mapped_column(
        ForeignKey("core.games.id", ondelete="CASCADE"), primary_key=True
    )
    ply: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    san: Mapped[str]
    uci: Mapped[str]
    fen_after: Mapped[str]
    clock_ms: Mapped[int | None]
    time_spent_ms: Mapped[int | None]


class ProcessingFailure(Base):
    """A raw version that a given parser version could not process."""

    __tablename__ = "processing_failures"
    __table_args__ = ({"schema": CORE},)

    raw_game_id: Mapped[int] = mapped_column(
        ForeignKey("raw.raw_games.id", ondelete="RESTRICT"), primary_key=True
    )
    parser_version: Mapped[int] = mapped_column(primary_key=True)
    error: Mapped[str]
    failed_at: Mapped[datetime] = mapped_column(server_default=func.now())
