"""The engine contract: what the rest of the system needs from a chess engine.

Everything here is engine-agnostic. `stockfish.py` implements `Engine` with a real
Stockfish process; tests use a scripted fake. Scores are always from White's point of
view, the convention stored in `analysis.position_evaluations` (DATA_MODEL §4.3).
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Protocol

import chess

THREADS = 1  # ADR-004: one thread, so the same nodes limit gives the same result every time
MULTIPV = 1  # only the best line in V1; MultiPV 2 is planned for V2


class EngineError(Exception):
    """The engine could not be started or stopped responding."""


@dataclass(frozen=True, slots=True)
class Score:
    """An evaluation from White's point of view: exactly one of `cp` or `mate` is set.

    cp:   centipawns, +100 ≈ White is a pawn better.
    mate: +n = White mates in n moves, -n = Black mates in n.
          0 = the side to move is already checkmated (a terminal position; who won
          follows from whose turn it is, which the caller knows).
    """

    cp: int | None = None
    mate: int | None = None

    def __post_init__(self) -> None:
        if (self.cp is None) == (self.mate is None):
            raise ValueError(f"exactly one of cp or mate must be set, got {self!r}")


@dataclass(frozen=True, slots=True)
class EngineResult:
    """The engine's verdict on one position."""

    score: Score
    best_move_uci: str | None  # None in terminal positions (no legal moves)
    pv_uci: tuple[str, ...]  # principal variation: the best line the engine found
    depth: int
    nodes: int


@dataclass(frozen=True, slots=True)
class EngineSettings:
    """Search settings that change results, so they are part of the analysis config."""

    nodes_per_position: int
    hash_mb: int = 64
    threads: int = THREADS
    multipv: int = MULTIPV

    def __post_init__(self) -> None:
        if self.nodes_per_position <= 0 or self.hash_mb < 1:
            raise ValueError(f"invalid engine settings: {self!r}")
        if self.threads != THREADS or self.multipv != MULTIPV:
            raise ValueError("V1 supports only threads=1 and multipv=1 (ADR-004)")


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    """Which engine produced the results, as reported by the engine itself."""

    name: str  # e.g. "stockfish"
    version: str  # e.g. "19"


def config_hash(identity: EngineIdentity, settings: EngineSettings) -> str:
    """SHA-256 identifying an analysis configuration (`analysis_configs.config_hash`).

    Same engine + version + settings → same hash → same `analysis_configs` row, so
    results from different runs are only ever mixed when they are truly comparable.
    """
    canonical = json.dumps(
        {"engine": asdict(identity), "settings": asdict(settings)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Engine(Protocol):
    @property
    def identity(self) -> EngineIdentity: ...

    @property
    def settings(self) -> EngineSettings: ...

    def new_game(self) -> None:
        """Forget everything from previous searches (hash table), so analysing a game
        never depends on which game was analysed before it."""
        ...

    def analyse(self, board: chess.Board) -> EngineResult:
        """Search a non-terminal position. The board's move stack is sent too, so the
        engine sees repetitions."""
        ...
