"""Chess.com JSON → ParsedGame. Payloads are synthetic but shaped like real archive games."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from chess_analytics.domain.game import (
    Color,
    GameResult,
    InvalidGameError,
    Outcome,
    TimeClass,
    UnsupportedGameError,
)
from chess_analytics.ingestion.chesscom.mapping import (
    opening_name_from_url,
    parse_time_control,
    result_from_codes,
    to_parsed_game,
)
from tests.samples import START, chesscom_game


def test_maps_a_complete_game() -> None:
    game = to_parsed_game(chesscom_game())

    assert (game.platform, game.platform_game_id) == ("chesscom", chesscom_game()["uuid"])
    assert (game.rules, game.time_class, game.rated) == ("chess", TimeClass.RAPID, True)
    assert (game.time_control, game.base_seconds, game.increment_seconds) == ("600", 600, 0)
    assert game.result is GameResult.BLACK_WIN
    assert game.termination == "player_b won by checkmate"
    assert game.start_time == datetime(2026, 10, 2, 18, 30, 5, tzinfo=UTC)  # from PGN headers
    assert game.end_time == datetime.fromtimestamp(1_790_015_405, tz=UTC)
    assert (game.source_eco, game.source_opening_name) == ("C00", "Kings Pawn Opening 1...e5")
    assert game.ply_count == 4
    assert game.moves[-1].san == "Qh4#"
    # Our FEN is always complete: halfmove clock 1 (Qh4 was no pawn move or capture), move 3.
    assert game.final_fen.endswith(" w KQkq - 1 3")


def test_maps_both_players() -> None:
    game = to_parsed_game(chesscom_game())

    assert (game.white.color, game.white.username, game.white.platform_uuid) == (
        Color.WHITE, "player_a", "uuid-a",
    )  # fmt: skip
    assert (game.white.result_code, game.white.outcome) == ("checkmated", Outcome.LOSS)
    assert (game.black.outcome, game.black.rating_after) == (Outcome.WIN, 1510)
    assert game.white.source_accuracy == Decimal("12.3")
    assert game.black.source_accuracy == Decimal("98.76")  # exact, no float noise


def test_accuracies_are_optional() -> None:
    game = to_parsed_game(chesscom_game(accuracies=None))
    assert game.white.source_accuracy is None and game.black.source_accuracy is None


def test_daily_game_uses_json_start_time_and_no_time_spent() -> None:
    game = to_parsed_game(
        chesscom_game(time_class="daily", time_control="1/86400", start_time=1_790_000_000)
    )
    assert (game.base_seconds, game.increment_seconds) == (86_400, None)
    assert game.start_time == datetime.fromtimestamp(1_790_000_000, tz=UTC)
    assert all(m.time_spent_ms is None for m in game.moves)
    assert game.moves[0].clock_ms == 598_000  # clocks are still kept


# ---------------------------------------------------------------- results ---


@pytest.mark.parametrize(
    ("white", "black", "expected"),
    [
        ("win", "resigned", GameResult.WHITE_WIN),
        ("timeout", "win", GameResult.BLACK_WIN),
        ("win", "abandoned", GameResult.WHITE_WIN),
        ("stalemate", "stalemate", GameResult.DRAW),
        ("timevsinsufficient", "timevsinsufficient", GameResult.DRAW),
        ("agreed", "agreed", GameResult.DRAW),
        ("50move", "50move", GameResult.DRAW),
    ],
)
def test_result_from_code_pairs(white: str, black: str, expected: GameResult) -> None:
    assert result_from_codes(white, black) is expected


@pytest.mark.parametrize(
    ("white", "black"),
    [("win", "win"), ("win", "agreed"), ("resigned", "timeout"), ("win", "mystery")],
)
def test_inconsistent_or_unknown_codes_are_invalid(white: str, black: str) -> None:
    with pytest.raises(InvalidGameError, match="result codes"):
        result_from_codes(white, black)


# ----------------------------------------------------------- time controls ---


@pytest.mark.parametrize(
    ("time_control", "time_class", "expected"),
    [
        ("600", TimeClass.RAPID, (600, 0)),
        ("180+2", TimeClass.BLITZ, (180, 2)),
        ("1/259200", TimeClass.DAILY, (259_200, None)),
    ],
)
def test_time_controls(time_control: str, time_class: TimeClass, expected: tuple) -> None:
    assert parse_time_control(time_control, time_class) == expected


@pytest.mark.parametrize(
    ("time_control", "time_class"),
    [("1/86400", TimeClass.RAPID), ("600", TimeClass.DAILY), ("2/86400", TimeClass.DAILY)],
)
def test_time_control_must_fit_the_time_class(time_control: str, time_class: TimeClass) -> None:
    with pytest.raises(InvalidGameError):
        parse_time_control(time_control, time_class)


# --------------------------------------------------------------- openings ---


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://www.chess.com/openings/Owens-Defense", "Owens Defense"),
        ("https://www.chess.com/openings/Sicilian-Defense-Najdorf/", "Sicilian Defense Najdorf"),
        (None, None),
        ("", None),
    ],
)
def test_opening_name_from_url(url: str | None, name: str | None) -> None:
    assert opening_name_from_url(url) == name


# ------------------------------------------------------------- rejections ---


def test_variants_are_unsupported() -> None:
    with pytest.raises(UnsupportedGameError, match="unsupported rules: oddschess"):
        to_parsed_game(chesscom_game(rules="oddschess"))


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"white": {"username": "a", "uuid": "u", "rating": 1, "result": "win"},
          "black": {"username": "b", "uuid": "v", "rating": 1, "result": "resigned"}},
         "result mismatch"),  # JSON says White won, PGN says 0-1
        ({"fen": START}, "final position"),
        ({"initial_setup": "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"}, "starting position"),
        ({"white": {"username": "a", "rating": 1, "result": "checkmated"}}, "no uuid"),
        ({"accuracies": {"white": 101, "black": 50}}, "out of range"),
        ({"time_class": "classical"}, "malformed"),
        ({"pgn": "1. e4 e5 2. Ke3 1-0"}, "not a finished game|invalid PGN"),
    ],
)  # fmt: skip
def test_inconsistent_games_are_rejected(overrides: dict, reason: str) -> None:
    with pytest.raises(InvalidGameError, match=reason):
        to_parsed_game(chesscom_game(**overrides))


def test_missing_field_is_invalid_not_a_crash() -> None:
    game = chesscom_game()
    del game["end_time"]
    with pytest.raises(InvalidGameError, match="malformed"):
        to_parsed_game(game)
