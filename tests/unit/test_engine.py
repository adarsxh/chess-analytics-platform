"""The engine contract and game evaluation, with a fake engine (no Stockfish needed)."""

import dataclasses

import chess
import pytest

from chess_analytics.engine.base import (
    EngineError,
    EngineIdentity,
    EngineSettings,
    Score,
    config_hash,
)
from chess_analytics.engine.game import evaluate_game, terminal_result
from chess_analytics.engine.stockfish import parse_identity
from tests.fakes import FakeEngine

START = chess.STARTING_FEN
FOOLS_MATE = ["f2f3", "e7e5", "g2g4", "d8h4"]  # Black mates on move 2
STALEMATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"  # Black to move, no legal moves, not check


# ---------------------------------------------------------------------- Score ---


@pytest.mark.parametrize("kwargs", [{}, {"cp": 10, "mate": 2}])
def test_score_needs_exactly_one_of_cp_or_mate(kwargs: dict) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Score(**kwargs)


def test_score_accepts_zero_values() -> None:
    assert Score(cp=0).cp == 0  # 0 is a value, not "missing"
    assert Score(mate=0).mate == 0


# ------------------------------------------------------------------- settings ---


@pytest.mark.parametrize(
    "kwargs",
    [
        {"nodes_per_position": 0},
        {"nodes_per_position": 1000, "hash_mb": 0},
        {"nodes_per_position": 1000, "threads": 2},
        {"nodes_per_position": 1000, "multipv": 2},
    ],
)
def test_invalid_engine_settings_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        EngineSettings(**kwargs)


def test_config_hash_is_stable_and_changes_with_every_field() -> None:
    identity = EngineIdentity(name="stockfish", version="19")
    settings = EngineSettings(nodes_per_position=300_000, hash_mb=64)
    base = config_hash(identity, settings)

    assert base == config_hash(EngineIdentity("stockfish", "19"), EngineSettings(300_000, 64))
    assert len(base) == 64
    variants = [
        config_hash(dataclasses.replace(identity, name="other"), settings),
        config_hash(dataclasses.replace(identity, version="18"), settings),
        config_hash(identity, dataclasses.replace(settings, nodes_per_position=100_000)),
        config_hash(identity, dataclasses.replace(settings, hash_mb=128)),
    ]
    assert base not in variants and len(set(variants)) == len(variants)


@pytest.mark.parametrize(
    ("id_name", "expected"),
    [
        ("Stockfish 19", EngineIdentity("stockfish", "19")),
        ("Stockfish 17.1", EngineIdentity("stockfish", "17.1")),
        ("Stockfish dev-20261122-3e8fde75", EngineIdentity("stockfish", "dev-20261122-3e8fde75")),
    ],
)
def test_engine_identity_is_read_from_the_uci_name(id_name: str, expected: EngineIdentity) -> None:
    assert parse_identity(id_name) == expected


def test_engine_identity_without_a_version_is_an_error() -> None:
    with pytest.raises(EngineError):
        parse_identity("Stockfish")


# ---------------------------------------------------------- terminal positions ---


def test_checkmate_is_mate_zero_without_searching() -> None:
    board = chess.Board()
    for uci in FOOLS_MATE:
        board.push_uci(uci)
    result = terminal_result(board)
    assert result is not None
    assert (result.score, result.best_move_uci, result.pv_uci) == (Score(mate=0), None, ())


@pytest.mark.parametrize(
    "fen",
    [
        STALEMATE_FEN,
        "8/8/8/4k3/8/8/8/4K3 w - - 0 1",  # bare kings: insufficient material
        "8/8/8/4k3/8/8/8/4KN2 w - - 0 1",  # king + knight: insufficient material
    ],
)
def test_draws_by_rule_score_zero_without_searching(fen: str) -> None:
    result = terminal_result(chess.Board(fen))
    assert result is not None and result.score == Score(cp=0)


def test_claimable_draws_are_still_searched() -> None:
    board = chess.Board()
    for uci in ["g1f3", "g8f6", "f3g1", "f6g8"] * 2:  # threefold repetition: claimable only
        board.push_uci(uci)
    assert board.can_claim_threefold_repetition()
    assert terminal_result(board) is None


# ---------------------------------------------------------------- evaluate_game ---


def test_every_position_is_evaluated_in_order() -> None:
    engine = FakeEngine()
    moves = ["e2e4", "d7d5", "e4d5", "d8d5"]

    evaluations = evaluate_game(engine, START, moves)

    assert [e.ply for e in evaluations] == [0, 1, 2, 3, 4]
    assert engine.new_games == 1
    assert [b.fen() for b in engine.analysed][0] == START
    # ply 3 (exd5): White is a pawn up; ply 4 (Qxd5): Black took it back
    assert [e.result.score.cp for e in evaluations] == [0, 0, 0, 100, 0]


def test_the_engine_sees_the_move_history() -> None:
    engine = FakeEngine()
    evaluate_game(engine, START, ["e2e4", "e7e5"])
    assert [m.uci() for m in engine.analysed[2].move_stack] == ["e2e4", "e7e5"]


def test_final_checkmate_is_not_sent_to_the_engine() -> None:
    engine = FakeEngine()

    evaluations = evaluate_game(engine, START, FOOLS_MATE)

    assert len(evaluations) == 5 and len(engine.analysed) == 4  # ply 4 is mate
    assert evaluations[-1].result.score == Score(mate=0)


def test_a_game_from_a_custom_position_starts_there() -> None:
    engine = FakeEngine()
    fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    evaluations = evaluate_game(engine, fen, ["e2e4"])
    assert engine.analysed[0].fen() == fen
    assert evaluations[0].result.score == Score(cp=100)


def test_an_engine_failure_propagates() -> None:
    with pytest.raises(EngineError):
        evaluate_game(FakeEngine(fail_after=2), START, ["e2e4", "e7e5", "g1f3"])
