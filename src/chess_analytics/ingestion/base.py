"""The `GameSource` contract: what every platform adapter provides (ARCHITECTURE.md).

The ingestion service depends only on this module, never on a specific platform. Adding
a platform means writing a new adapter that satisfies `GameSource`; nothing else changes.

The types are deliberately thin: games travel as the platform's untouched JSON payload,
because the raw layer stores exactly what the source sent (ADR-015). Interpreting that
payload is the parser's job (Phase 4), not the adapter's.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

# ------------------------------------------------------------------- types ---


@dataclass(frozen=True, slots=True)
class SourcePlayer:
    """A player's profile as the platform reports it."""

    platform: str
    username: str  # as displayed by the platform; may change over time
    platform_player_id: int | None = None  # numeric id, if the platform has one


@dataclass(frozen=True, slots=True)
class ArchiveRef:
    """One monthly archive of a player's games."""

    month: date  # always the first day of the month
    url: str  # opaque to everyone except the adapter that produced it

    def __post_init__(self) -> None:
        if self.month.day != 1:
            raise ValueError(f"archive month must be the first of the month, got {self.month}")


@dataclass(frozen=True, slots=True)
class SourceGame:
    """One game exactly as received, plus the platform's id for it."""

    platform_game_id: str
    payload: dict[str, Any] = field(repr=False)  # large; keep it out of logs and reprs

    def __post_init__(self) -> None:
        if not self.platform_game_id:
            raise ValueError("platform_game_id must not be empty")


@dataclass(frozen=True, slots=True)
class ArchiveFetchResult:
    """The outcome of one conditional fetch of an archive.

    Either the archive changed (`games` holds its full content, `etag` its new version
    tag) or it is unchanged since `etag` was issued (`not_modified`, no games).
    """

    archive: ArchiveRef
    http_status: int  # recorded in raw.archive_fetches for auditing
    etag: str | None
    games: tuple[SourceGame, ...] = ()

    def __post_init__(self) -> None:
        if self.not_modified and self.games:
            raise ValueError("a not-modified result cannot contain games")

    @property
    def not_modified(self) -> bool:
        return self.http_status == 304


# ------------------------------------------------------------------ errors ---


class SourceError(Exception):
    """Base class for every failure an adapter reports."""


class PlayerNotFoundError(SourceError):
    """The platform has no player with this username. Retrying will not help."""


class SourceUnavailableError(SourceError):
    """The platform failed or rate-limited us even after retries. Retrying later may help."""


# ---------------------------------------------------------------- contract ---


class GameSource(Protocol):
    """What the ingestion service needs from a platform.

    Implementations handle transport concerns themselves (timeouts, retries, rate
    limits, conditional requests) and raise only `SourceError` subclasses.
    """

    platform: str  # one of storage.schema.PLATFORMS, e.g. "chesscom"

    def get_player(self, username: str) -> SourcePlayer:
        """Look up a player's profile. Raises PlayerNotFoundError."""
        ...

    def list_archives(self, username: str) -> list[ArchiveRef]:
        """All monthly archives for the player, oldest first."""
        ...

    def fetch_archive(self, archive: ArchiveRef, *, etag: str | None = None) -> ArchiveFetchResult:
        """Fetch one archive; with `etag`, an unchanged archive returns not_modified."""
        ...
