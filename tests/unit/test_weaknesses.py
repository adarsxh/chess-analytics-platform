"""The weakness definitions (ADR-030): phase, position and opponent bands."""

import pytest

from chess_analytics.analytics import weaknesses as weak

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
ROOK_AND_MINORS = "4k3/2n5/8/8/8/2B5/8/R3K2R w - - 0 40"  # 4 pieces: R, R, B, n


def test_pieces_counts_queens_rooks_bishops_knights_only() -> None:
    assert weak.pieces(START) == 14
    assert weak.pieces(ROOK_AND_MINORS) == 4
    assert weak.pieces("4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3 w - - 0 30") == 0  # pawns, kings


@pytest.mark.parametrize(
    ("board", "ply", "white_first", "phase"),
    [
        (START, 1, True, "opening"),  # move 1
        (START, 19, True, "opening"),  # White's 10th move
        (START, 20, True, "opening"),  # Black's 10th move
        (START, 21, True, "middlegame"),  # move 11
        (START, 1, False, "opening"),  # Black starts: ply 1 is move 1
        (START, 20, False, "middlegame"),  # ... and ply 20 is move 11
        (ROOK_AND_MINORS, 5, True, "endgame"),  # few pieces: endgame even in move 3
        ("r3k2r/2n5/8/8/8/2B5/8/R3K2R w - - 0 40", 60, True, "endgame"),  # exactly 6
        ("r3k2r/2n5/8/8/8/2B5/8/R2QK2R w - - 0 40", 60, True, "middlegame"),  # 7
    ],
)
def test_phase(board: str, ply: int, white_first: bool, phase: str) -> None:
    assert weak.phase(board, ply, white_first) == phase


@pytest.mark.parametrize(
    ("win", "position"),
    [(100, "winning"), (70, "winning"), (69.9, "equal"), (30.1, "equal"), (30, "losing"),
     (0, "losing")],
)  # fmt: skip
def test_position_thresholds_are_inclusive(win: float, position: str) -> None:
    assert weak.position(win) == position


def test_mover_win_percent_is_from_the_movers_side() -> None:
    assert weak.mover_win_percent(0, None, mover_is_white=True) == 50
    white_better = weak.mover_win_percent(300, None, mover_is_white=True)
    assert white_better > 70  # +3 pawns ≈ 75 %
    assert weak.mover_win_percent(300, None, mover_is_white=False) == pytest.approx(
        100 - white_better
    )
    assert weak.mover_win_percent(None, 3, mover_is_white=False) == 0  # White mates
    assert weak.mover_win_percent(None, -2, mover_is_white=False) == 100  # Black mates


@pytest.mark.parametrize(
    ("difference", "band"),
    [(-300, "much_lower"), (-25, "much_lower"), (-24, "lower"), (-10, "lower"),
     (-9, "similar"), (0, "similar"), (9, "similar"), (10, "higher"), (24, "higher"),
     (25, "much_higher")],
)  # fmt: skip
def test_rating_bands(difference: int, band: str) -> None:
    assert weak.rating_band(difference) == band


def test_pre_game_difference_undoes_the_rating_change() -> None:
    # I was 1500 and won 8 points; my opponent ended on 1492, so they started near 1500.
    assert weak.pre_game_difference(1508, 1500, 1492) == 0
    # Post-game the gap looks like −16, which would put a win against an equal in "lower".
    assert weak.rating_band(1492 - 1508) == "lower"
    assert weak.rating_band(weak.pre_game_difference(1508, 1500, 1492)) == "similar"
