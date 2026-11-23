"""In-memory test doubles for the project's contracts."""

from collections.abc import Callable
from dataclasses import dataclass, field

import chess

from chess_analytics.engine.base import (
    EngineError,
    EngineIdentity,
    EngineResult,
    EngineSettings,
    Score,
)
from chess_analytics.ingestion.base import (
    ArchiveFetchResult,
    ArchiveRef,
    PlayerNotFoundError,
    SourceGame,
    SourcePlayer,
    SourceUnavailableError,
)


@dataclass
class FakeGameSource:
    """A `GameSource` backed by dicts. Archive ETags are derived from their content,
    so changing an archive's games changes its ETag, just like the real platform."""

    platform: str = "chesscom"
    players: dict[str, SourcePlayer] = field(default_factory=dict)  # key: lower-case username
    archives: dict[str, list[ArchiveRef]] = field(default_factory=dict)
    games: dict[str, tuple[SourceGame, ...]] = field(default_factory=dict)  # key: archive url
    fetched: list[str] = field(default_factory=list)  # archive urls, in request order
    failing_urls: set[str] = field(default_factory=set)  # these archives raise on fetch

    def get_player(self, username: str) -> SourcePlayer:
        try:
            return self.players[username.lower()]
        except KeyError:
            raise PlayerNotFoundError(username) from None

    def list_archives(self, username: str) -> list[ArchiveRef]:
        return sorted(self.archives.get(username.lower(), []), key=lambda a: a.month)

    def fetch_archive(self, archive: ArchiveRef, *, etag: str | None = None) -> ArchiveFetchResult:
        self.fetched.append(archive.url)
        if archive.url in self.failing_urls:
            raise SourceUnavailableError(f"{archive.url} returned 503 after 4 attempts")
        games = self.games.get(archive.url, ())
        current = f'"{hash(tuple(g.platform_game_id for g in games))}"'
        if etag == current:
            return ArchiveFetchResult(archive=archive, http_status=304, etag=current)
        return ArchiveFetchResult(archive=archive, http_status=200, etag=current, games=games)


def material_score(board: chess.Board) -> Score:
    """A cheap, deterministic stand-in for an engine evaluation: material balance."""
    values = {
        chess.PAWN: 100,
        chess.KNIGHT: 300,
        chess.BISHOP: 300,
        chess.ROOK: 500,
        chess.QUEEN: 900,
    }
    cp = sum(
        value * (len(board.pieces(piece, chess.WHITE)) - len(board.pieces(piece, chess.BLACK)))
        for piece, value in values.items()
    )
    return Score(cp=cp)


@dataclass
class FakeEngine:
    """An `Engine` that scores positions with `score_fn` (material by default) and records
    every call, so tests can check what was searched without running Stockfish."""

    score_fn: Callable[[chess.Board], Score] = material_score
    identity: EngineIdentity = EngineIdentity(name="fake", version="1")
    settings: EngineSettings = EngineSettings(nodes_per_position=1000)
    analysed: list[chess.Board] = field(default_factory=list)  # copies, with move stacks
    new_games: int = 0
    fail_after: int | None = None  # raise EngineError after this many analyse calls
    closed: bool = False

    def new_game(self) -> None:
        self.new_games += 1

    def analyse(self, board: chess.Board) -> EngineResult:
        if self.fail_after is not None and len(self.analysed) >= self.fail_after:
            raise EngineError("fake engine crashed")
        assert not board.is_game_over(), "terminal positions must not reach the engine"
        self.analysed.append(board.copy())
        best = next(iter(board.legal_moves)).uci()
        return EngineResult(
            score=self.score_fn(board), best_move_uci=best, pv_uci=(best,), depth=1, nodes=1
        )

    def close(self) -> None:
        self.closed = True
