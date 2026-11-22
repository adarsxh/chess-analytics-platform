"""Evaluate every position of a game: ply 0 (start) through ply N (after the last move)."""

from collections.abc import Sequence
from dataclasses import dataclass

import chess

from chess_analytics.engine.base import Engine, EngineResult, Score


@dataclass(frozen=True, slots=True)
class PositionEvaluation:
    ply: int  # 0 = the position before any move; ply k = after the k-th move
    result: EngineResult


def terminal_result(board: chess.Board) -> EngineResult | None:
    """The result for a position where the game is over by the rules, without searching.

    Engines can't search a position with no legal moves, and there is nothing to search:
    checkmate is decided (mate 0: the side to move is mated) and the draws are draws.
    Positions that are merely *claimable* draws (threefold, 50 moves) are searched as
    normal; the engine sees the move history and scores repetitions itself.
    """
    outcome = board.outcome(claim_draw=False)
    if outcome is None:
        return None
    # Anything other than checkmate is a draw: stalemate, insufficient material, the
    # 75-move rule, fivefold repetition.
    mated = outcome.termination == chess.Termination.CHECKMATE
    score = Score(mate=0) if mated else Score(cp=0)
    return EngineResult(score=score, best_move_uci=None, pv_uci=(), depth=0, nodes=0)


def evaluate_game(
    engine: Engine, initial_fen: str, moves_uci: Sequence[str]
) -> list[PositionEvaluation]:
    """Evaluate the start position and the position after each move, in order.

    Starts with `new_game()` so the result depends only on this game, never on what the
    engine analysed before: with one thread and a nodes limit, the same game gives the
    same evaluations every time (ADR-004).
    """
    engine.new_game()
    board = chess.Board(initial_fen)
    evaluations = []
    for ply in range(len(moves_uci) + 1):
        if ply > 0:
            board.push(chess.Move.from_uci(moves_uci[ply - 1]))
        result = terminal_result(board) or engine.analyse(board)
        evaluations.append(PositionEvaluation(ply=ply, result=result))
    return evaluations
