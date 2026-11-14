"""Platform-neutral PGN replay. All PGNs here are hand-written."""

import pytest

from chess_analytics.domain.game import InvalidGameError
from chess_analytics.parsing.pgn import moves_hash, parse_pgn

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

# Scholar's mate with clocks (3 min + 2 s increment).
SCHOLARS_MATE = """[Result "1-0"]

1. e4 {[%clk 0:03:00]} 1... e5 {[%clk 0:02:59.5]} 2. Bc4 {[%clk 0:02:55.2]}
2... Nc6 {[%clk 0:02:58]} 3. Qh5 {[%clk 0:02:50]} 3... Nf6 {[%clk 0:02:40]}
4. Qxf7# {[%clk 0:02:49.9]} 1-0
"""


def test_replays_every_move_with_san_uci_and_positions() -> None:
    parsed = parse_pgn(SCHOLARS_MATE)

    assert [m.ply for m in parsed.moves] == list(range(1, 8))
    assert [m.san for m in parsed.moves] == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    assert parsed.moves[0].uci == "e2e4"
    assert parsed.moves[-1].uci == "h5f7"
    assert parsed.initial_fen == START
    assert parsed.final_fen == parsed.moves[-1].fen_after
    assert parsed.final_fen.startswith("r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR b")


def test_clocks_and_time_spent_with_increment() -> None:
    parsed = parse_pgn(SCHOLARS_MATE, base_seconds=180, increment_seconds=2)
    clocks = [m.clock_ms for m in parsed.moves]
    spent = [m.time_spent_ms for m in parsed.moves]

    assert clocks[:3] == [180_000, 179_500, 175_200]  # tenths of a second kept
    # spent = previous own clock - this clock + increment; first move uses the base time.
    assert spent[0] == 180_000 - 180_000 + 2_000  # instant move still earns the increment
    assert spent[2] == 180_000 - 175_200 + 2_000  # White's 2nd move: 6.8 s
    assert spent[3] == 179_500 - 178_000 + 2_000  # Black's 2nd move: 3.5 s


def test_without_base_time_spent_is_unknown_but_clocks_are_kept() -> None:
    # Daily games: the clock is not a single countdown, so time spent can't be derived.
    parsed = parse_pgn(SCHOLARS_MATE)
    assert parsed.moves[0].clock_ms == 180_000
    assert all(m.time_spent_ms is None for m in parsed.moves)


def test_moves_without_clock_comments() -> None:
    parsed = parse_pgn('[Result "0-1"]\n\n1. f3 e5 2. g4 Qh4# 0-1')
    assert parsed.moves[-1].san == "Qh4#"
    assert all(m.clock_ms is None and m.time_spent_ms is None for m in parsed.moves)


def test_custom_starting_position_is_honoured() -> None:
    fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    parsed = parse_pgn(f'[Result "1/2-1/2"]\n[SetUp "1"]\n[FEN "{fen}"]\n\n1. e4 Kd7 1/2-1/2')
    assert parsed.initial_fen == fen
    assert [m.san for m in parsed.moves] == ["e4", "Kd7"]


def test_game_ending_before_any_move() -> None:
    parsed = parse_pgn('[Result "0-1"]\n\n0-1')
    assert parsed.moves == ()
    assert parsed.final_fen == parsed.initial_fen == START


def test_headers_are_passed_through_for_the_platform_mapper() -> None:
    parsed = parse_pgn('[Result "1-0"]\n[ECO "C20"]\n[UTCDate "2026.10.02"]\n\n1. e4 1-0')
    assert parsed.headers["ECO"] == "C20"
    assert parsed.headers["UTCDate"] == "2026.10.02"


# ------------------------------------------------------------- strictness ---


@pytest.mark.parametrize(
    ("pgn", "reason"),
    [
        ('[Result "1-0"]\n\n1. e4 e5 2. Ke3 1-0', "illegal san"),  # would be truncated silently
        ("hello world", "not a finished game"),  # parses as an empty game by default
        ("", "empty PGN"),
        ('[Result "*"]\n\n1. e4 *', "not a finished game"),
        ('[Result "1-0"]\n[SetUp "1"]\n[FEN "not a fen"]\n\n1. e4 1-0', "(?i)fen"),
    ],
)
def test_invalid_games_are_rejected_not_truncated(pgn: str, reason: str) -> None:
    with pytest.raises(InvalidGameError, match=reason):
        parse_pgn(pgn)


# ------------------------------------------------------------- moves hash ---


def test_moves_hash_identifies_the_exact_sequence() -> None:
    base = moves_hash(START, ["e2e4", "e7e5"])
    assert base == moves_hash(START, ["e2e4", "e7e5"])
    assert base != moves_hash(START, ["e2e4", "c7c5"])  # different move
    assert base != moves_hash(START, ["e2e4"])  # truncated game
    assert base != moves_hash("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1", ["e2e4", "e7e5"])  # other start
    assert parse_pgn(SCHOLARS_MATE).moves_hash == moves_hash(
        START, ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]
    )
