"""Move classification: the win-% curve, thresholds and per-move verdicts (no engine)."""

from decimal import Decimal

import chess
import pytest

from chess_analytics.classification.moves import (
    Label,
    classify_moves,
    label_for,
    white_win_percent,
)
from chess_analytics.engine.base import EngineResult, Score

START = chess.STARTING_FEN
# White: Ka1; Black: Ka8, Qb2 (unprotected, giving check). Kxb2 is White's only legal move.
ONLY_ONE_MOVE = "k7/8/8/8/8/8/1q6/K7 w - - 0 1"
# 1.e4 e5 2.Bc4 Nc6 3.Qf3 Nd4: White to play Qxf7#.
WHITE_MATES_IN_ONE = "r1bqkbnr/pppp1ppp/8/4p3/2BnP3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4"


def ev(cp: int | None = None, mate: int | None = None, best: str | None = "a1a1") -> EngineResult:
    """An engine result with the given score. `best` defaults to a move nobody plays."""
    return EngineResult(
        score=Score(cp=cp, mate=mate), best_move_uci=best, pv_uci=(), depth=1, nodes=1
    )


def only(verdicts: list) -> object:
    assert len(verdicts) == 1
    return verdicts[0]


# --------------------------------------------------------------- win % curve ---


def test_equal_position_is_fifty_percent() -> None:
    assert white_win_percent(Score(cp=0), white_to_move=True) == 50


@pytest.mark.parametrize("cp", [35, 100, 300, 999])
def test_the_curve_is_symmetric(cp: int) -> None:
    white = white_win_percent(Score(cp=cp), white_to_move=True)
    assert white + white_win_percent(Score(cp=-cp), white_to_move=True) == pytest.approx(100)
    assert white > 50


@pytest.mark.parametrize(("cp", "expected"), [(100, 59.1), (300, 75.1), (600, 90.1), (900, 96.5)])
def test_known_points_on_the_curve(cp: int, expected: float) -> None:
    assert white_win_percent(Score(cp=cp), white_to_move=True) == pytest.approx(expected, abs=0.05)


def test_scores_beyond_the_clamp_are_equal() -> None:
    at_clamp = white_win_percent(Score(cp=1000), white_to_move=True)
    assert white_win_percent(Score(cp=5000), white_to_move=True) == at_clamp < 100


@pytest.mark.parametrize(
    ("score", "white_to_move", "expected"),
    [
        (Score(mate=3), True, 100),  # White mates
        (Score(mate=-1), True, 0),  # Black mates
        (Score(mate=0), True, 0),  # White to move and checkmated
        (Score(mate=0), False, 100),  # Black to move and checkmated
    ],
)
def test_mate_scores(score: Score, white_to_move: bool, expected: float) -> None:
    assert white_win_percent(score, white_to_move=white_to_move) == expected


# ---------------------------------------------------------------- thresholds ---


@pytest.mark.parametrize(
    ("loss", "label"),
    [
        ("0", Label.EXCELLENT),
        ("1.99", Label.EXCELLENT),
        ("2.00", Label.GOOD),
        ("9.99", Label.GOOD),
        ("10.00", Label.INACCURACY),
        ("19.99", Label.INACCURACY),
        ("20.00", Label.MISTAKE),
        ("29.99", Label.MISTAKE),
        ("30.00", Label.BLUNDER),
        ("100.00", Label.BLUNDER),
    ],
)
def test_threshold_boundaries(loss: str, label: Label) -> None:
    assert label_for(Decimal(loss)) == label


# -------------------------------------------------------------- classify_moves ---


def test_relaxing_when_completely_winning_is_not_a_blunder() -> None:
    """+900 → +600: 300 cp lost, but only ~6 % win chance (the ADR-024 example)."""
    verdict = only(classify_moves(START, ["e2e4"], [ev(cp=900), ev(cp=600)]))
    assert verdict.label == Label.GOOD
    assert verdict.win_loss == Decimal("6.38")


def test_the_same_300_cp_in_an_equal_game_is_a_mistake() -> None:
    verdict = only(classify_moves(START, ["e2e4"], [ev(cp=0), ev(cp=-300)]))
    assert verdict.label == Label.MISTAKE
    assert verdict.win_loss == Decimal("25.11")


def test_black_moves_are_judged_from_blacks_point_of_view() -> None:
    evaluations = [ev(cp=0), ev(cp=0), ev(cp=300), ev(cp=-300)]
    verdicts = classify_moves(START, ["e2e4", "e7e5", "g1f3"], evaluations)
    # Black's e5 let White go from 0 to +300 (Black lost ~25 %); White's Nf3 then
    # dropped from +300 to -300 (White lost ~50 %).
    assert [(v.ply, v.label) for v in verdicts] == [
        (1, Label.EXCELLENT),
        (2, Label.MISTAKE),
        (3, Label.BLUNDER),
    ]


def test_a_move_that_improves_the_eval_loses_nothing() -> None:
    # The engine underestimated the move before playing it (search horizon): no negative loss.
    verdict = only(classify_moves(START, ["e2e4"], [ev(cp=0), ev(cp=80)]))
    assert (verdict.label, verdict.win_loss) == (Label.EXCELLENT, Decimal("0.00"))


def test_the_engines_first_choice_is_best_even_with_some_loss() -> None:
    # Re-searching the next position can drift a little; the engine's own move stays "best".
    verdict = only(classify_moves(START, ["e2e4"], [ev(cp=40, best="e2e4"), ev(cp=0)]))
    assert verdict.label == Label.BEST and verdict.win_loss > 0


def test_the_only_legal_move_is_forced_whatever_it_costs() -> None:
    verdict = only(classify_moves(ONLY_ONE_MOVE, ["a1b2"], [ev(cp=0), ev(cp=-900)]))
    assert verdict.label == Label.FORCED


def test_delivering_checkmate_loses_nothing() -> None:
    evaluations = [ev(mate=1, best="f3f7"), ev(mate=0, best=None)]
    verdict = only(classify_moves(WHITE_MATES_IN_ONE, ["f3f7"], evaluations))
    assert (verdict.label, verdict.win_loss) == (Label.BEST, Decimal("0.00"))


def test_missing_a_mate_but_staying_winning_is_minor() -> None:
    verdict = only(classify_moves(WHITE_MATES_IN_ONE, ["f3e2"], [ev(mate=1), ev(cp=800)]))
    assert verdict.label == Label.GOOD


def test_allowing_a_forced_mate_from_equality_is_a_blunder() -> None:
    verdict = only(classify_moves(START, ["f2f3"], [ev(cp=0), ev(mate=-2)]))
    assert (verdict.label, verdict.win_loss) == (Label.BLUNDER, Decimal("50.00"))


def test_the_label_is_derived_from_the_stored_rounded_loss() -> None:
    for verdict in classify_moves(START, ["e2e4"], [ev(cp=0), ev(cp=-45)]):
        assert verdict.win_loss == verdict.win_loss.quantize(Decimal("0.01"))
        assert verdict.label == label_for(verdict.win_loss)


def test_one_evaluation_per_position_is_required() -> None:
    with pytest.raises(ValueError, match="one evaluation per position"):
        classify_moves(START, ["e2e4", "e7e5"], [ev(cp=0), ev(cp=0)])
