"""Platform-neutral PGN parsing: replay every move with python-chess.

Input is standard PGN (headers + movetext, optional `[%clk]` comments). Output is the
move list with SAN, UCI, position after each move, clocks and time spent, plus the
headers for the platform mapper to interpret. Nothing here knows about any platform.

Strict by design (decision P4-a): python-chess is lenient by default (an illegal move
stops parsing and returns a *partial* game; arbitrary text parses as an empty game),
so this module checks for both and raises InvalidGameError instead.
"""

import hashlib
import io
import logging
from dataclasses import dataclass

import chess
import chess.pgn

from chess_analytics.domain.game import InvalidGameError, ParsedMove

FINISHED_RESULTS = frozenset({"1-0", "0-1", "1/2-1/2"})


@dataclass(frozen=True, slots=True)
class ParsedPgn:
    headers: dict[str, str]
    initial_fen: str
    final_fen: str
    moves: tuple[ParsedMove, ...]
    moves_hash: str


def parse_pgn(
    pgn: str,
    *,
    base_seconds: int | None = None,
    increment_seconds: int | None = None,
) -> ParsedPgn:
    """Replay a finished game. `base_seconds`/`increment_seconds` enable time-spent
    calculation; without a base (e.g. daily games) time spent is left unknown."""
    game = _read(pgn)
    result = game.headers.get("Result", "*")
    if result not in FINISHED_RESULTS:
        raise InvalidGameError(f"not a finished game (Result {result!r})")

    board = game.board()  # honours [SetUp]/[FEN] for non-standard starting positions
    initial_fen = board.fen()
    last_clock_ms: dict[chess.Color, int | None] = {
        chess.WHITE: None if base_seconds is None else base_seconds * 1000,
        chess.BLACK: None if base_seconds is None else base_seconds * 1000,
    }
    increment_ms = (increment_seconds or 0) * 1000

    moves: list[ParsedMove] = []
    for ply, node in enumerate(game.mainline(), start=1):
        mover = board.turn
        san = board.san(node.move)
        board.push(node.move)

        clock = node.clock()  # seconds as float, e.g. 598.8, or None without [%clk]
        clock_ms = None if clock is None else round(clock * 1000)
        previous = last_clock_ms[mover]
        spent = None
        # Only meaningful for a single countdown clock (base + increment). Daily games give
        # each move a fresh allowance, so consecutive clocks can't be subtracted.
        if base_seconds is not None and clock_ms is not None and previous is not None:
            spent = previous - clock_ms + increment_ms
        last_clock_ms[mover] = clock_ms

        moves.append(
            ParsedMove(
                ply=ply,
                san=san,
                uci=node.move.uci(),
                fen_after=board.fen(),
                clock_ms=clock_ms,
                time_spent_ms=spent,
            )
        )

    return ParsedPgn(
        headers=dict(game.headers),
        initial_fen=initial_fen,
        final_fen=board.fen(),
        moves=tuple(moves),
        moves_hash=moves_hash(initial_fen, [m.uci for m in moves]),
    )


def moves_hash(initial_fen: str, uci_moves: list[str]) -> str:
    """Identifies a game's exact move sequence. Engine analyses store it, so an analysis
    made for a different move list is detectable as stale (ADR-017)."""
    canonical = initial_fen + "\n" + " ".join(uci_moves)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read(pgn: str) -> chess.pgn.Game:
    # python-chess also logs each parse error; we report errors ourselves, so silence it.
    pgn_logger = logging.getLogger("chess.pgn")
    previous_level = pgn_logger.level
    pgn_logger.setLevel(logging.CRITICAL)
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
    except (ValueError, KeyError) as exc:  # e.g. an invalid [FEN] header
        raise InvalidGameError(f"unreadable PGN: {exc}") from exc
    finally:
        pgn_logger.setLevel(previous_level)

    if game is None:
        raise InvalidGameError("empty PGN")
    if game.errors:
        # Parsing stopped early: storing the moves read so far would silently truncate it.
        raise InvalidGameError(f"invalid PGN: {game.errors[0]}")
    return game
