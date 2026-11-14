"""raw → core processing end to end, on the real schema, with synthetic Chess.com games."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.services import process as process_module
from chess_analytics.services.process import PARSER_VERSION, process_pending
from chess_analytics.storage.models.core import (
    Game,
    GameMove,
    GamePlayer,
    Player,
    ProcessingFailure,
)
from tests.samples import chesscom_game

from .factories import make_raw_game

pytestmark = pytest.mark.integration


def add_raw(session_factory: sessionmaker[Session], payload: dict) -> int:
    with session_factory.begin() as session:
        return make_raw_game(session, platform_game_id=payload.get("uuid", "x"), payload=payload).id


def count(session_factory: sessionmaker[Session], model: type) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_valid_game_is_written_to_core(session_factory) -> None:
    raw_id = add_raw(session_factory, chesscom_game(uuid="game-1"))
    players_before = count(session_factory, Player)  # the factory's run needs a player too

    summary = process_pending(session_factory)

    assert (summary.pending, summary.processed, summary.rejected) == (1, 1, 0)
    with session_factory() as session:
        game = session.scalar(select(Game))
        assert (game.platform_game_id, game.raw_game_id) == ("game-1", raw_id)
        assert (game.parser_version, game.result, game.ply_count) == (
            PARSER_VERSION,
            "black_win",
            4,
        )
    assert count(session_factory, GamePlayer) == 2
    assert count(session_factory, GameMove) == 4
    assert count(session_factory, Player) == players_before + 2  # both sides became players


def test_processing_twice_does_nothing_the_second_time(session_factory) -> None:
    add_raw(session_factory, chesscom_game(uuid="game-1"))
    process_pending(session_factory)

    again = process_pending(session_factory)

    assert (again.pending, again.processed) == (0, 0)
    assert count(session_factory, Game) == 1


def test_variant_is_rejected_once_and_recorded(session_factory) -> None:
    raw_id = add_raw(session_factory, chesscom_game(uuid="odds-1", rules="oddschess"))

    summary = process_pending(session_factory)

    assert (summary.processed, summary.rejected) == (0, 1)
    with session_factory() as session:
        failure = session.scalar(select(ProcessingFailure))
        assert (failure.raw_game_id, failure.parser_version) == (raw_id, PARSER_VERSION)
        assert failure.error == "UnsupportedGameError: unsupported rules: oddschess"
    assert count(session_factory, Game) == 0
    assert process_pending(session_factory).pending == 0  # not retried every run


def test_invalid_game_is_rejected_without_partial_rows(session_factory) -> None:
    add_raw(session_factory, chesscom_game(uuid="bad-1", fen="8/8/8/8/8/8/8/8 w - -"))
    players_before = count(session_factory, Player)

    summary = process_pending(session_factory)

    assert summary.rejected == 1
    assert count(session_factory, Game) == 0
    assert count(session_factory, Player) == players_before  # no players from rejected games


def test_one_bad_game_does_not_block_the_others(session_factory) -> None:
    add_raw(session_factory, chesscom_game(uuid="odds-1", rules="oddschess"))
    add_raw(session_factory, chesscom_game(uuid="game-2"))

    summary = process_pending(session_factory)

    assert (summary.processed, summary.rejected) == (1, 1)


def test_bumping_the_parser_version_reprocesses_everything(session_factory) -> None:
    add_raw(session_factory, chesscom_game(uuid="game-1"))
    add_raw(session_factory, chesscom_game(uuid="odds-1", rules="oddschess"))
    process_pending(session_factory, parser_version=1)

    summary = process_pending(session_factory, parser_version=2)

    assert (summary.pending, summary.processed, summary.rejected) == (2, 1, 1)
    with session_factory() as session:
        assert session.scalar(select(Game.parser_version)) == 2
    assert count(session_factory, Game) == 1  # refreshed in place, not duplicated


def test_unexpected_errors_stop_the_run_and_leave_nothing_half_written(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_raw(session_factory, chesscom_game(uuid="game-1"))

    def broken_mapper(payload):
        raise RuntimeError("bug in a mapper")

    monkeypatch.setitem(process_module.MAPPERS, "chesscom", broken_mapper)
    with pytest.raises(RuntimeError, match="bug in a mapper"):
        process_pending(session_factory)

    assert count(session_factory, Game) == 0
    assert count(session_factory, ProcessingFailure) == 0  # a bug is not a data rejection
