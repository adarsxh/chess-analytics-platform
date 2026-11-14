"""Chess.com game JSON → platform-neutral `ParsedGame`.

All knowledge of Chess.com's game format lives here: result codes, time-control
syntax, where the start time is, what the `eco` field contains. Facts verified on
2,418 real games (DATA_MODEL.md §7 and the Phase 4 profiling):
- results come as a pair: one side "win" + a loss code, or both sides a draw code;
- `start_time` exists only for daily games; the PGN's UTCDate+UTCTime hold the same
  instant (checked on daily games), so they are used for live games;
- `eco` is a URL to Chess.com's opening page, the ECO code is in the PGN header;
- Chess.com's `fen` sometimes omits the move counters and writes the en-passant square
  after every double pawn push, so positions are compared on placement, side to move
  and castling rights, which is what identifies the position.

Strict (decision P4-a): any inconsistency raises InvalidGameError; unsupported rules
raise UnsupportedGameError (decision P1-b, ADR-006).
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from chess_analytics.domain.game import (
    Color,
    GameResult,
    InvalidGameError,
    Outcome,
    ParsedGame,
    ParsedPlayer,
    TimeClass,
    UnsupportedGameError,
)
from chess_analytics.parsing.pgn import parse_pgn

PLATFORM = "chesscom"
SUPPORTED_RULES = frozenset({"chess"})

# Chess.com's documented result codes, by meaning. Variant-only codes
# (kingofthehill, threecheck, bughousepartnerlose) can't occur for rules == "chess".
WIN_CODE = "win"
LOSS_CODES = frozenset({"checkmated", "resigned", "timeout", "abandoned", "lose"})
DRAW_CODES = frozenset(
    {"agreed", "repetition", "stalemate", "insufficient", "timevsinsufficient", "50move"}
)
PGN_RESULTS = {"1-0": GameResult.WHITE_WIN, "0-1": GameResult.BLACK_WIN,
               "1/2-1/2": GameResult.DRAW}  # fmt: skip


def to_parsed_game(payload: Mapping[str, Any]) -> ParsedGame:
    """Map one raw Chess.com game. Raises GameRejectedError subclasses only."""
    rules = payload.get("rules")
    if rules not in SUPPORTED_RULES:
        raise UnsupportedGameError(f"unsupported rules: {rules}")
    try:
        return _map(payload)
    except (KeyError, TypeError, ValueError) as exc:  # missing or malformed fields
        raise InvalidGameError(f"malformed Chess.com game: {type(exc).__name__}: {exc}") from exc


def _map(payload: Mapping[str, Any]) -> ParsedGame:
    time_class = TimeClass(payload["time_class"])
    base_seconds, increment_seconds = parse_time_control(payload["time_control"], time_class)
    is_daily = time_class is TimeClass.DAILY

    pgn = parse_pgn(
        payload["pgn"],
        # Daily clocks are per-move allowances, not one countdown: no time-spent.
        base_seconds=None if is_daily else base_seconds,
        increment_seconds=None if is_daily else increment_seconds,
    )

    result = result_from_codes(payload["white"]["result"], payload["black"]["result"])
    if PGN_RESULTS[pgn.headers["Result"]] is not result:
        raise InvalidGameError(
            f"result mismatch: JSON says {result}, PGN says {pgn.headers['Result']}"
        )
    if not same_position(pgn.initial_fen, payload["initial_setup"]):
        raise InvalidGameError("starting position differs from initial_setup")
    if not same_position(pgn.final_fen, payload["fen"]):
        raise InvalidGameError("replayed final position differs from Chess.com's fen")

    accuracies = payload.get("accuracies") or {}
    return ParsedGame(
        platform=PLATFORM,
        platform_game_id=payload["uuid"],
        url=payload["url"],
        rules=payload["rules"],
        time_class=time_class,
        time_control=payload["time_control"],
        base_seconds=base_seconds,
        increment_seconds=increment_seconds,
        rated=bool(payload["rated"]),
        start_time=_start_time(payload, pgn.headers),
        end_time=datetime.fromtimestamp(payload["end_time"], tz=UTC),
        result=result,
        termination=pgn.headers.get("Termination"),
        initial_fen=pgn.initial_fen,
        final_fen=pgn.final_fen,
        moves=pgn.moves,
        moves_hash=pgn.moves_hash,
        source_eco=pgn.headers.get("ECO"),
        source_opening_name=opening_name_from_url(payload.get("eco")),
        white=_player(payload["white"], Color.WHITE, result, accuracies.get("white")),
        black=_player(payload["black"], Color.BLACK, result, accuracies.get("black")),
    )


# ------------------------------------------------------------------ pieces ---


def result_from_codes(white_code: str, black_code: str) -> GameResult:
    if white_code == WIN_CODE and black_code in LOSS_CODES:
        return GameResult.WHITE_WIN
    if black_code == WIN_CODE and white_code in LOSS_CODES:
        return GameResult.BLACK_WIN
    if white_code in DRAW_CODES and black_code in DRAW_CODES:
        return GameResult.DRAW
    raise InvalidGameError(f"inconsistent or unknown result codes: {white_code}/{black_code}")


def outcome_for(color: Color, result: GameResult) -> Outcome:
    if result is GameResult.DRAW:
        return Outcome.DRAW
    won = (result is GameResult.WHITE_WIN) == (color is Color.WHITE)
    return Outcome.WIN if won else Outcome.LOSS


def parse_time_control(time_control: str, time_class: TimeClass) -> tuple[int, int | None]:
    """ "600" → (600, 0); "180+2" → (180, 2); daily "1/86400" → (86400, None),
    i.e. seconds allowed per move and no increment (DATA_MODEL: "daily → base only")."""
    if "/" in time_control:
        moves, seconds = time_control.split("/")
        if time_class is not TimeClass.DAILY or moves != "1":
            raise InvalidGameError(f"unexpected time control {time_control!r} for {time_class}")
        return int(seconds), None
    if time_class is TimeClass.DAILY:
        raise InvalidGameError(f"daily game with live time control {time_control!r}")
    base, _, increment = time_control.partition("+")
    return int(base), int(increment or 0)


def same_position(fen_a: str, fen_b: str) -> bool:
    """Same piece placement, side to move and castling rights (the first three FEN
    fields). En-passant notation and move counters are conventions that differ."""
    return fen_a.split()[:3] == fen_b.split()[:3]


def opening_name_from_url(url: str | None) -> str | None:
    """'https://www.chess.com/openings/Kings-Pawn-Opening-1...e5' → 'Kings Pawn Opening 1...e5'.

    Approximate (decision P3-a): the slug drops punctuation such as apostrophes, so
    "King's" becomes "Kings". Source data until openings are classified by us (ADR-016).
    """
    if not url:
        return None
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").strip() or None


def _start_time(payload: Mapping[str, Any], headers: Mapping[str, str]) -> datetime | None:
    if "start_time" in payload:  # daily games
        return datetime.fromtimestamp(payload["start_time"], tz=UTC)
    date, time = headers.get("UTCDate"), headers.get("UTCTime")
    if not date or not time:
        return None
    return datetime.strptime(f"{date} {time}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC)


def _player(
    side: Mapping[str, Any], color: Color, result: GameResult, accuracy: Any
) -> ParsedPlayer:
    if not side.get("uuid"):
        raise InvalidGameError(f"{color} player has no uuid")
    return ParsedPlayer(
        color=color,
        platform_uuid=side["uuid"],
        username=side["username"],
        rating_after=side.get("rating"),
        result_code=side["result"],
        outcome=outcome_for(color, result),
        source_accuracy=_accuracy(accuracy),
    )


def _accuracy(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        accuracy = Decimal(str(value))  # via str: 67.45 stays 67.45, not 67.4500000001
    except InvalidOperation as exc:
        raise InvalidGameError(f"invalid accuracy {value!r}") from exc
    if not 0 <= accuracy <= 100:
        raise InvalidGameError(f"accuracy out of range: {value!r}")
    return accuracy
