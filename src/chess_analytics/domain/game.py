"""Platform-neutral game model: what every platform's games are translated into.

These types carry no storage or platform details. A platform mapper builds a
`ParsedGame`; the core repository stores it. Nothing here imports from the project.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Color(StrEnum):
    WHITE = "white"
    BLACK = "black"


class GameResult(StrEnum):
    WHITE_WIN = "white_win"
    BLACK_WIN = "black_win"
    DRAW = "draw"


class Outcome(StrEnum):
    """A result from one player's point of view."""

    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


class TimeClass(StrEnum):
    BULLET = "bullet"
    BLITZ = "blitz"
    RAPID = "rapid"
    DAILY = "daily"


# ------------------------------------------------------------------ errors ---


class GameRejectedError(Exception):
    """A game that will not be processed. The reason is recorded, not retried
    until the parser changes (DATA_MODEL.md: core.processing_failures)."""


class UnsupportedGameError(GameRejectedError):
    """Valid source data that V1 deliberately does not process (e.g. variants, ADR-006)."""


class InvalidGameError(GameRejectedError):
    """Source data that is inconsistent or cannot be replayed (illegal move, mismatch)."""


# ------------------------------------------------------------------- types ---


@dataclass(frozen=True, slots=True)
class ParsedMove:
    ply: int  # 1-based: 1 = White's first move, 2 = Black's first move, ...
    san: str  # "Nf3"
    uci: str  # "g1f3"
    fen_after: str
    clock_ms: int | None  # mover's remaining time after the move
    time_spent_ms: int | None  # time the mover used for this move


@dataclass(frozen=True, slots=True)
class ParsedPlayer:
    color: Color
    platform_uuid: str
    username: str
    rating_after: int | None  # Chess.com: rating *after* the game (verified, DATA_MODEL §7)
    result_code: str  # as received, e.g. "timevsinsufficient"
    outcome: Outcome
    source_accuracy: Decimal | None


@dataclass(frozen=True, slots=True)
class ParsedGame:
    platform: str
    platform_game_id: str
    url: str
    rules: str
    time_class: TimeClass
    time_control: str  # as received: "600", "180+2", "1/86400"
    base_seconds: int | None
    increment_seconds: int | None
    rated: bool
    start_time: datetime | None
    end_time: datetime
    result: GameResult
    termination: str | None
    initial_fen: str
    final_fen: str
    moves: tuple[ParsedMove, ...]
    moves_hash: str
    source_eco: str | None
    source_opening_name: str | None
    white: ParsedPlayer
    black: ParsedPlayer

    @property
    def ply_count(self) -> int:
        return len(self.moves)
