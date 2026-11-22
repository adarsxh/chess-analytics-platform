"""StockfishEngine against a real Stockfish binary.

Small node limits keep these fast; the point is the adapter (UCI conversation, score
conversion, reproducibility), not playing strength.
"""

from collections.abc import Iterator

import chess
import pytest

from chess_analytics.engine.base import EngineError, EngineSettings, Score
from chess_analytics.engine.game import evaluate_game
from chess_analytics.engine.stockfish import PV_MOVES_KEPT, StockfishEngine

pytestmark = pytest.mark.engine

SETTINGS = EngineSettings(nodes_per_position=20_000, hash_mb=16)
# 1.e4 e5 2.Bc4 Nc6 3.Qf3: White threatens Qxf7#; here it is White to play it.
WHITE_MATES_IN_ONE = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
SHORT_GAME = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1", "f8e7"]


@pytest.fixture
def engine(stockfish_path: str) -> Iterator[StockfishEngine]:
    with StockfishEngine(stockfish_path, SETTINGS) as engine:
        yield engine


def test_identity_comes_from_the_engine(engine: StockfishEngine) -> None:
    assert engine.identity.name == "stockfish"
    assert engine.identity.version  # e.g. "19"; recorded in analysis_configs


def test_start_position(engine: StockfishEngine) -> None:
    result = engine.analyse(chess.Board())

    assert result.score.cp is not None and abs(result.score.cp) < 100  # roughly equal
    assert chess.Move.from_uci(result.best_move_uci) in chess.Board().legal_moves
    assert result.pv_uci[0] == result.best_move_uci and len(result.pv_uci) <= PV_MOVES_KEPT
    assert result.depth >= 8
    assert result.nodes >= SETTINGS.nodes_per_position  # stops at the limit, not before


def test_mate_scores_are_from_whites_point_of_view(engine: StockfishEngine) -> None:
    white_to_mate = engine.analyse(chess.Board(WHITE_MATES_IN_ONE))
    assert (white_to_mate.score, white_to_mate.best_move_uci) == (Score(mate=1), "f3f7")

    board = chess.Board()
    for uci in ["f2f3", "e7e5", "g2g4"]:  # Black to play Qh4#
        board.push_uci(uci)
    black_to_mate = engine.analyse(board)
    assert (black_to_mate.score, black_to_mate.best_move_uci) == (Score(mate=-1), "d8h4")


def test_a_losing_position_is_negative_for_white(engine: StockfishEngine) -> None:
    black_queen_up = chess.Board("4k3/8/8/8/8/8/q7/4K3 w - - 0 1")  # queen out of reach
    score = engine.analyse(black_queen_up).score
    # K+Q vs K: depending on depth, a big negative cp or a forced mate for Black
    assert (score.mate is not None and score.mate < 0) or (score.cp is not None and score.cp < -500)


def test_the_same_game_gives_identical_evaluations(stockfish_path: str) -> None:
    """ADR-004 reproducibility: one thread + a nodes limit + a fresh hash per game."""
    with StockfishEngine(stockfish_path, SETTINGS) as first:
        a = evaluate_game(first, chess.STARTING_FEN, SHORT_GAME)
        evaluate_game(first, chess.STARTING_FEN, ["d2d4", "d7d5"])  # something in between
        b = evaluate_game(first, chess.STARTING_FEN, SHORT_GAME)
    with StockfishEngine(stockfish_path, SETTINGS) as second:
        c = evaluate_game(second, chess.STARTING_FEN, SHORT_GAME)

    assert a == b == c
    assert len(a) == len(SHORT_GAME) + 1


def test_a_missing_binary_is_an_engine_error() -> None:
    with pytest.raises(EngineError, match="cannot start engine"):
        StockfishEngine("/nonexistent/stockfish", SETTINGS)


def test_a_dead_engine_is_an_engine_error(engine: StockfishEngine) -> None:
    engine._engine.protocol.transport.kill()  # simulate a crash (e.g. killed by the OS)
    with pytest.raises(EngineError, match="engine failed"):
        engine.analyse(chess.Board())


def test_settings_are_built_from_configuration(make_settings, stockfish_path: str) -> None:
    settings = make_settings(
        engine_path=stockfish_path, engine_nodes_per_position=5_000, engine_hash_mb=8
    )
    with StockfishEngine.from_settings(settings) as engine:
        assert engine.settings == EngineSettings(nodes_per_position=5_000, hash_mb=8)
