"""`Engine` implemented with a Stockfish process, spoken to over UCI by python-chess.

UCI (Universal Chess Interface) is a text protocol on stdin/stdout:
    → position fen <fen> moves e2e4 e7e5     → go nodes 300000
    ← info depth 18 score cp 35 pv f1b5 …    ← bestmove f1b5
python-chess's `chess.engine` handles that conversation for us.
"""

from concurrent.futures import CancelledError
from types import TracebackType
from typing import Self

import chess
import chess.engine

from chess_analytics.config import Settings
from chess_analytics.engine.base import (
    EngineError,
    EngineIdentity,
    EngineResult,
    EngineSettings,
    Score,
)

# What python-chess raises when the engine misbehaves or its process dies (a dead process
# cancels the pending request, hence CancelledError).
_ENGINE_FAILURES = (chess.engine.EngineError, CancelledError)
PV_MOVES_KEPT = 10  # enough to show the idea of the best line, small enough to store per ply


def parse_identity(id_name: str) -> EngineIdentity:
    """'Stockfish 19' → (stockfish, 19); 'Stockfish dev-20261122-3e8fde75' → (stockfish, dev-…)."""
    name, _, version = id_name.strip().partition(" ")
    if not name or not version:
        raise EngineError(f"cannot read engine name and version from {id_name!r}")
    return EngineIdentity(name=name.lower(), version=version)


class StockfishEngine:
    """One Stockfish process. Use as a context manager so the process is always stopped."""

    def __init__(self, path: str, settings: EngineSettings) -> None:
        self._settings = settings
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(path)
        except (OSError, chess.engine.EngineError) as exc:
            raise EngineError(f"cannot start engine {path!r}: {exc}") from exc
        try:
            self._identity = parse_identity(self._engine.id.get("name", ""))
            self._engine.configure({"Threads": settings.threads, "Hash": settings.hash_mb})
        except BaseException:
            self._engine.quit()
            raise
        self._limit = chess.engine.Limit(nodes=settings.nodes_per_position)
        self._game: object = object()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            settings.engine_path,
            EngineSettings(
                nodes_per_position=settings.engine_nodes_per_position,
                hash_mb=settings.engine_hash_mb,
            ),
        )

    @property
    def identity(self) -> EngineIdentity:
        return self._identity

    @property
    def settings(self) -> EngineSettings:
        return self._settings

    def new_game(self) -> None:
        # python-chess sends `ucinewgame` (which clears Stockfish's hash table) whenever
        # the `game` key differs from the previous search's.
        self._game = object()

    def analyse(self, board: chess.Board) -> EngineResult:
        try:
            info = self._engine.analyse(board, self._limit, game=self._game)
        except _ENGINE_FAILURES as exc:
            raise EngineError(f"engine failed: {exc!r}") from exc
        pov = info["score"].white()
        score = Score(mate=pov.mate()) if pov.is_mate() else Score(cp=pov.score())
        pv = tuple(move.uci() for move in info.get("pv", ())[:PV_MOVES_KEPT])
        return EngineResult(
            score=score,
            best_move_uci=pv[0] if pv else None,
            pv_uci=pv,
            depth=info.get("depth", 0),
            nodes=info.get("nodes", 0),
        )

    def close(self) -> None:
        try:
            self._engine.quit()
        except _ENGINE_FAILURES:
            self._engine.close()  # already dead: just release the process handle

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
