"""Minimal valid rows for constraint tests.

Each factory inserts (flushes) one valid row, plus any parent rows it needs, and returns
it. Tests override only the fields they are about, which keeps each test focused on one rule.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chess_analytics.domain.game import (
    Color,
    GameResult,
    Outcome,
    ParsedGame,
    ParsedMove,
    ParsedPlayer,
    TimeClass,
)
from chess_analytics.storage.models.analysis import AnalysisConfig, GameAnalysis
from chess_analytics.storage.models.core import Game, Player
from chess_analytics.storage.models.raw import ArchiveFetch, IngestionRun, RawGame

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _unique() -> str:
    return uuid.uuid4().hex


def add(session: Session, obj: Any) -> Any:
    session.add(obj)
    session.flush()  # send the INSERT now, so constraint errors surface here
    return obj


def make_player(session: Session, **overrides: Any) -> Player:
    values = {"platform": "chesscom", "platform_uuid": _unique(), "username": f"u_{_unique()[:8]}"}
    return add(session, Player(**(values | overrides)))


def make_archive_fetch(session: Session) -> ArchiveFetch:
    player = make_player(session)
    run = add(session, IngestionRun(player_id=player.id, mode="incremental", status="running"))
    return add(
        session,
        ArchiveFetch(
            ingestion_run_id=run.id,
            player_id=player.id,
            archive_month=date(2026, 10, 1),
            url="https://api.chess.com/pub/player/x/games/2026/10",
            http_status=200,
        ),
    )


def make_raw_game(session: Session, **overrides: Any) -> RawGame:
    values = {
        "platform": "chesscom",
        "platform_game_id": _unique(),
        "content_hash": _unique(),
        "payload": {"uuid": "x", "rules": "chess"},
        "source_fetch_id": make_archive_fetch(session).id,
    }
    return add(session, RawGame(**(values | overrides)))


def make_game(session: Session, **overrides: Any) -> Game:
    raw = make_raw_game(session)
    values = {
        "platform": "chesscom",
        "platform_game_id": raw.platform_game_id,
        "url": "https://www.chess.com/game/live/1",
        "raw_game_id": raw.id,
        "parser_version": 1,
        "rules": "chess",
        "time_class": "rapid",
        "time_control": "600",
        "base_seconds": 600,
        "rated": True,
        "end_time": datetime(2026, 10, 2, tzinfo=UTC),
        "result": "white_win",
        "initial_fen": START_FEN,
        "final_fen": START_FEN,
        "ply_count": 0,
        "moves_hash": _unique(),
    }
    return add(session, Game(**(values | overrides)))


def make_analysis_config(session: Session, **overrides: Any) -> AnalysisConfig:
    values = {
        "engine_name": "stockfish",
        "engine_version": "17.1",
        "nodes_per_position": 1_000_000,
        "threads": 1,
        "hash_mb": 64,
        "multipv": 1,
        "config_hash": _unique(),
    }
    return add(session, AnalysisConfig(**(values | overrides)))


def make_game_analysis(session: Session, game: Game, **overrides: Any) -> GameAnalysis:
    values = {
        "game_id": game.id,
        "analysis_config_id": make_analysis_config(session).id,
        "moves_hash": game.moves_hash,
    }
    return add(session, GameAnalysis(**(values | overrides)))


def parsed_moves(*sans_and_ucis: tuple[str, str]) -> tuple[ParsedMove, ...]:
    return tuple(
        ParsedMove(
            ply=i,
            san=san,
            uci=uci,
            fen_after=f"fen-{i}",
            clock_ms=600_000 - i * 1000,
            time_spent_ms=1000,
        )  # fmt: skip
        for i, (san, uci) in enumerate(sans_and_ucis, start=1)
    )


def make_parsed_game(**overrides: Any) -> ParsedGame:
    """A platform-neutral game as a mapper would produce it (not stored)."""
    game_id = overrides.pop("platform_game_id", _unique())
    values = {
        "platform": "chesscom",
        "platform_game_id": game_id,
        "url": f"https://www.chess.com/game/live/{game_id}",
        "rules": "chess",
        "time_class": TimeClass.RAPID,
        "time_control": "600",
        "base_seconds": 600,
        "increment_seconds": 0,
        "rated": True,
        "start_time": datetime(2026, 10, 2, 18, 0, tzinfo=UTC),
        "end_time": datetime(2026, 10, 2, 18, 10, tzinfo=UTC),
        "result": GameResult.WHITE_WIN,
        "termination": "white won by resignation",
        "initial_fen": START_FEN,
        "final_fen": "fen-2",
        "moves": parsed_moves(("e4", "e2e4"), ("e5", "e7e5")),
        "moves_hash": _unique(),
        "source_eco": "C20",
        "source_opening_name": "Kings Pawn Opening",
        "white": ParsedPlayer(
            Color.WHITE, "uuid-white", "WhitePlayer", 1500, "win", Outcome.WIN, None
        ),  # fmt: skip
        "black": ParsedPlayer(
            Color.BLACK, "uuid-black", "BlackPlayer", 1480, "resigned", Outcome.LOSS, None
        ),  # fmt: skip
    }
    return ParsedGame(**(values | overrides))


@contextmanager
def violates(session: Session, constraint: str):
    """Assert that the statements in the block fail on exactly this named constraint.

    Runs inside a SAVEPOINT, so the session stays usable after the expected failure.
    """
    with pytest.raises(IntegrityError) as caught, session.begin_nested():
        yield
        session.flush()
    actual = caught.value.orig.diag.constraint_name
    assert actual == constraint, f"expected {constraint!r} to fire, got {actual!r}"
