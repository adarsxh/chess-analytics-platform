"""Accuracy %: how close a player's moves were to the engine's, on a 0–100 scale (ADR-026).

Built on the win-% loss already stored per move by the classifier (ADR-024):

    move accuracy = 103.1668 · e^(−0.04354 · loss) − 3.1669, clamped to 0–100
    game accuracy = (arithmetic mean + harmonic mean) / 2 over the player's moves,
                    forced moves excluded; in the harmonic mean a move counts as ≥ 1

The curve is the one Lichess publishes. A perfect move (loss 0) scores 100, a 10 % loss
about 64, a 30 % loss about 25. The plain mean alone is generous (a blunder is diluted by
the easy moves around it, ~16–20 points above Chess.com on our games); the harmonic mean
is dominated by the worst moves. Averaging both is close to Lichess, which also weights by
position volatility (not done here). The floor of 1 keeps one hopeless move from making
the harmonic mean 0.

Computed in SQL at read time (analytics/queries.py); these Python functions define the
same formula and are what the tests check SQL against.
"""

import math
from collections.abc import Iterable

ACCURACY_SCALE = 103.1668100711649
ACCURACY_DECAY = 0.04354415386753951
ACCURACY_OFFSET = -3.166924740191411
HARMONIC_FLOOR = 1.0


def move_accuracy(win_loss: float) -> float:
    raw = ACCURACY_SCALE * math.exp(-ACCURACY_DECAY * win_loss) + ACCURACY_OFFSET
    return max(0.0, min(100.0, raw))


def game_accuracy(win_losses: Iterable[float]) -> float | None:
    """(mean + harmonic mean) / 2 of the move accuracies; None if there are no moves."""
    values = [move_accuracy(loss) for loss in win_losses]
    if not values:
        return None
    mean = sum(values) / len(values)
    harmonic = len(values) / sum(1 / max(v, HARMONIC_FLOOR) for v in values)
    return (mean + harmonic) / 2
