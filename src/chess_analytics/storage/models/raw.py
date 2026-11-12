"""`raw` schema: what the source sent, never rewritten (DATA_MODEL.md §4.1, ADR-015)."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, SmallInteger, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from chess_analytics.storage.schema import (
    PLATFORMS,
    RAW,
    Base,
    CreatedAtMixin,
    IdMixin,
    one_of,
)


class IngestionRun(IdMixin, CreatedAtMixin, Base):
    """One sync of one player: the domain record of ingestion (ADR-018)."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        one_of("mode", ("incremental", "full_refresh")),
        one_of("status", ("running", "succeeded", "failed")),
        Index(None, "player_id", "started_at"),  # sync history of a player
        {"schema": RAW},
    )

    player_id: Mapped[int] = mapped_column(ForeignKey("core.players.id", ondelete="RESTRICT"))
    mode: Mapped[str]
    month_from: Mapped[date | None]
    month_to: Mapped[date | None]
    status: Mapped[str]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    archives_checked: Mapped[int] = mapped_column(server_default="0")
    archives_not_modified: Mapped[int] = mapped_column(server_default="0")
    games_seen: Mapped[int] = mapped_column(server_default="0")
    new_raw_versions: Mapped[int] = mapped_column(server_default="0")
    games_processed: Mapped[int] = mapped_column(server_default="0")
    games_failed: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None]


class ArchiveFetch(IdMixin, Base):
    """One HTTP request for one monthly archive: audit log and ETag state."""

    __tablename__ = "archive_fetches"
    __table_args__ = (
        # "What did we get last time for this player and month?" → latest ETag.
        Index(None, "player_id", "archive_month", "fetched_at"),
        {"schema": RAW},
    )

    ingestion_run_id: Mapped[int] = mapped_column(
        ForeignKey("raw.ingestion_runs.id", ondelete="RESTRICT")
    )
    player_id: Mapped[int] = mapped_column(ForeignKey("core.players.id", ondelete="RESTRICT"))
    archive_month: Mapped[date]  # first day of the month
    url: Mapped[str]
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())
    http_status: Mapped[int] = mapped_column(SmallInteger)
    etag: Mapped[str | None]
    games_in_archive: Mapped[int | None]  # null on 304 Not Modified
    new_versions_stored: Mapped[int] = mapped_column(server_default="0")


class RawGame(IdMixin, Base):
    """One distinct version of a game's source payload. Payloads are never modified."""

    __tablename__ = "raw_games"
    __table_args__ = (
        UniqueConstraint("platform", "platform_game_id", "content_hash"),
        one_of("platform", PLATFORMS),
        {"schema": RAW},
    )

    platform: Mapped[str]
    platform_game_id: Mapped[str]
    content_hash: Mapped[str]  # SHA-256 of canonical (sorted-keys) JSON
    payload: Mapped[dict[str, Any]]
    source_fetch_id: Mapped[int] = mapped_column(
        ForeignKey("raw.archive_fetches.id", ondelete="RESTRICT")
    )
    first_fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # The only mutable column: bumped when this exact version is fetched again.
    # Current version of a game = highest last_seen_at.
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
