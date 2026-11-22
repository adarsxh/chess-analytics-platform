"""Move classification by win-probability loss (ADR-024).

1. Every position's engine score becomes the expected win % for White, via the logistic
   curve Lichess publishes. A pawn matters a lot in an equal game, very little when one
   side is already winning, which raw centipawns don't capture.
2. A move's loss = the mover's win % before it (the engine's best play) minus the mover's
   win % after it (the move actually played), never below 0.
3. The loss, rounded to 0.01, picks the label. The stored `eval_loss` is that rounded
   number, so a label can always be re-derived from the row.

These are our own classifications, not a reproduction of Chess.com's labels. Thresholds
change only together with CLASSIFIER_VERSION (stored per row, ADR-017).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

import chess

from chess_analytics.engine.base import EngineResult, Score

CLASSIFIER_VERSION = 1

# Lichess's fit of win rate against centipawns (lila, WinPercent). Scores beyond ±1000 cp
# are clamped: at that point the game is decided and extra centipawns carry no meaning.
WIN_CURVE_SLOPE = 0.00368208
CP_CLAMP = 1000


class Label(StrEnum):
    FORCED = "forced"  # the only legal move: no decision was made
    BEST = "best"  # the engine's first choice
    EXCELLENT = "excellent"
    GOOD = "good"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"


# Lowest win-% loss (inclusive) for each label, worst first. Below the last: EXCELLENT.
THRESHOLDS: tuple[tuple[Decimal, Label], ...] = (
    (Decimal(30), Label.BLUNDER),
    (Decimal(20), Label.MISTAKE),
    (Decimal(10), Label.INACCURACY),
    (Decimal(2), Label.GOOD),
)


@dataclass(frozen=True, slots=True)
class MoveVerdict:
    ply: int  # 1 = the first move of the game
    label: Label
    win_loss: Decimal  # mover's win-% loss, 0.00–100.00 (stored as eval_loss)


def white_win_percent(score: Score, *, white_to_move: bool) -> float:
    """Expected win % for White (0–100) in a position with this engine score."""
    if score.mate is not None:
        if score.mate == 0:  # the side to move is checkmated
            return 0.0 if white_to_move else 100.0
        return 100.0 if score.mate > 0 else 0.0
    assert score.cp is not None
    cp = max(-CP_CLAMP, min(CP_CLAMP, score.cp))
    return 50 + 50 * (2 / (1 + math.exp(-WIN_CURVE_SLOPE * cp)) - 1)


def label_for(win_loss: Decimal) -> Label:
    for minimum, label in THRESHOLDS:
        if win_loss >= minimum:
            return label
    return Label.EXCELLENT


def classify_moves(
    initial_fen: str, moves_uci: Sequence[str], evaluations: Sequence[EngineResult]
) -> list[MoveVerdict]:
    """Classify every move of a game, both players'.

    `evaluations[k]` is the engine result for the position after k moves (0 = start), as
    produced by `engine.game.evaluate_game`.
    """
    if len(evaluations) != len(moves_uci) + 1:
        raise ValueError(
            f"need one evaluation per position: {len(moves_uci)} moves → "
            f"{len(moves_uci) + 1} positions, got {len(evaluations)}"
        )
    board = chess.Board(initial_fen)
    verdicts = []
    for ply, uci in enumerate(moves_uci, start=1):
        before, after = evaluations[ply - 1], evaluations[ply]
        mover_is_white = board.turn == chess.WHITE
        forced = board.legal_moves.count() == 1

        white_before = white_win_percent(before.score, white_to_move=mover_is_white)
        white_after = white_win_percent(after.score, white_to_move=not mover_is_white)
        mover_before = white_before if mover_is_white else 100 - white_before
        mover_after = white_after if mover_is_white else 100 - white_after
        win_loss = Decimal(max(0.0, mover_before - mover_after)).quantize(Decimal("0.01"))

        if forced:
            label = Label.FORCED
        elif uci == before.best_move_uci:
            label = Label.BEST
        else:
            label = label_for(win_loss)
        verdicts.append(MoveVerdict(ply=ply, label=label, win_loss=win_loss))
        board.push(chess.Move.from_uci(uci))
    return verdicts
