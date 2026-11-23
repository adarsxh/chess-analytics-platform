"""The accuracy formula (ADR-026). The SQL version is checked against these functions in
tests/integration/test_analytics.py."""

import pytest

from chess_analytics.analytics.accuracy import game_accuracy, move_accuracy


@pytest.mark.parametrize(
    ("loss", "expected"),
    [(0, 100.0), (5, 79.8), (10, 63.6), (30, 24.8), (60, 4.4), (100, 0.0)],
)
def test_move_accuracy_curve(loss: float, expected: float) -> None:
    assert move_accuracy(loss) == pytest.approx(expected, abs=0.1)


def test_move_accuracy_stays_within_0_and_100() -> None:
    assert all(0 <= move_accuracy(loss) <= 100 for loss in range(0, 101))


def test_game_accuracy_averages_mean_and_harmonic_mean() -> None:
    # Move accuracies 100 and 24.77: mean 62.39, harmonic 2/(1/100 + 1/24.77) ≈ 39.71.
    assert game_accuracy([0, 30]) == pytest.approx((62.39 + 39.71) / 2, abs=0.05)


def test_one_blunder_costs_more_than_in_a_plain_mean() -> None:
    losses = [0] * 19 + [30]
    plain_mean = sum(move_accuracy(x) for x in losses) / len(losses)
    assert game_accuracy(losses) < plain_mean - 4  # 91.5 vs 96.2


def test_a_hopeless_move_does_not_zero_the_game() -> None:
    # Accuracies 100, 100, 100, 0: mean 75; harmonic 4/(3/100 + 1/1) ≈ 3.9 (0 counts as 1).
    assert game_accuracy([0, 0, 0, 100]) == pytest.approx((75 + 3.88) / 2, abs=0.05)


def test_no_moves_no_accuracy() -> None:
    assert game_accuracy([]) is None
