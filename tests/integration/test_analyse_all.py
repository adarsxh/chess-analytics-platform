""" "Analyse all" (ADR-028): request, expand into bulk jobs, stop, progress, worker handler."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.domain.game import Color, Outcome, ParsedPlayer
from chess_analytics.engine.base import EngineError, EngineSettings
from chess_analytics.services.analyse import (
    analyse_game,
    analysis_progress,
    cancel_analyse_all,
    queue_all_for_analysis,
    queue_latest_for_analysis,
    request_analyse_all,
    request_analysis,
)
from chess_analytics.storage.models.ops import Job
from chess_analytics.storage.repositories.jobs import JobRepository
from chess_analytics.storage.repositories.queries import GameQueries
from chess_analytics.worker.handlers import (
    EngineHolder,
    PermanentJobError,
    RetryableJobError,
    handle_analyse_player,
)
from tests.fakes import FakeEngine

from .factories import store_game

pytestmark = pytest.mark.integration

BASE = datetime(2026, 10, 1, tzinfo=UTC)
ME = {"platform": "chesscom", "username": "WhitePlayer"}


@pytest.fixture
def games(session_factory: sessionmaker[Session]) -> list[int]:
    """Four games of WhitePlayer, oldest first."""
    return [store_game(session_factory, end_time=BASE + timedelta(days=d)) for d in range(4)]


@pytest.fixture
def player_id(session_factory, games) -> int:
    with session_factory() as session:
        return GameQueries(session).find_player(**ME).id


def jobs_by_game(session_factory) -> dict[int, tuple[str, int]]:
    """game_id → (status, priority) of its analyse_game job."""
    with session_factory() as session:
        jobs = session.scalars(select(Job).where(Job.job_type == "analyse_game")).all()
        return {job.payload["game_id"]: (job.status, job.priority) for job in jobs}


def expand(session_factory, engine=None, username="WhitePlayer") -> list[int] | None:
    return queue_all_for_analysis(
        platform="chesscom", username=username, engine=engine or FakeEngine(),
        session_factory=session_factory,
    )  # fmt: skip


def progress(session_factory, player_id: int):
    with session_factory() as session:
        return analysis_progress(session, player_id=player_id, **ME)


def cancel(session_factory, player_id: int) -> int:
    return cancel_analyse_all(session_factory, **ME)


# ---------------------------------------------------------------- request ---


def test_analyse_all_is_one_job_per_player(session_factory, games) -> None:
    job_id, created = request_analyse_all(session_factory, **ME)
    again, created_again = request_analyse_all(session_factory, platform="chesscom",
                                               username="whiteplayer")  # fmt: skip

    assert created and not created_again and again == job_id
    assert request_analyse_all(session_factory, platform="chesscom", username="nobody") is None
    assert cancel_analyse_all(session_factory, platform="chesscom", username="nobody") is None
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert (job.job_type, job.priority, job.dedup_key) == (
            "analyse_player", 100, "analyse_all:chesscom:whiteplayer",
        )  # fmt: skip
        assert job.payload == ME


# ----------------------------------------------------------------- expand ---


def test_expansion_queues_every_unanalysed_game_as_bulk(session_factory, games) -> None:
    analyse_game(games[0], engine=FakeEngine(), session_factory=session_factory)

    assert expand(session_factory) == [games[3], games[2], games[1]]  # newest first
    assert jobs_by_game(session_factory) == {g: ("queued", 300) for g in games[1:]}
    assert expand(session_factory) == [games[3], games[2], games[1]]  # again: no duplicates
    assert len(jobs_by_game(session_factory)) == 3
    assert expand(session_factory, username="nobody") is None


def test_expansion_uses_the_workers_engine_config(session_factory, games) -> None:
    engine = FakeEngine()
    for game in games:
        analyse_game(game, engine=engine, session_factory=session_factory)

    assert expand(session_factory, engine) == []
    stronger = dataclasses.replace(engine, settings=EngineSettings(nodes_per_position=7))
    assert len(expand(session_factory, stronger)) == 4  # a new config has analysed nothing


def test_games_already_queued_keep_their_higher_priority(session_factory, games) -> None:
    request_analysis(session_factory, games[0])  # opened by the user: on demand
    with session_factory() as session:
        player_id = GameQueries(session).find_player(**ME).id
    queue_latest_for_analysis(player_id, latest=1, engine=FakeEngine(),
                              session_factory=session_factory)  # fmt: skip

    expand(session_factory)

    assert jobs_by_game(session_factory) == {
        games[0]: ("queued", 100), games[3]: ("queued", 200),
        games[1]: ("queued", 300), games[2]: ("queued", 300),
    }  # fmt: skip


def test_bulk_work_runs_after_on_demand_and_after_sync_work(session_factory, games) -> None:
    expand(session_factory)  # bulk, queued first
    with session_factory() as session:
        player_id = GameQueries(session).find_player(**ME).id
    other = store_game(session_factory, end_time=BASE + timedelta(days=9))  # a new game
    queue_latest_for_analysis(player_id, latest=1, engine=FakeEngine(),
                              session_factory=session_factory)  # fmt: skip

    with session_factory.begin() as session:
        claimed = JobRepository(session).claim("w1", lease=timedelta(minutes=1))
        assert claimed.payload == {"game_id": other}


# ------------------------------------------------------------------- stop ---


def test_stop_cancels_only_queued_bulk_analyses(session_factory, games, player_id) -> None:
    expand(session_factory)
    request_analysis(session_factory, games[0])  # someone opened this game: moved ahead
    with session_factory.begin() as session:  # a worker is analysing games[3]
        session.execute(
            update(Job)
            .where(Job.payload["game_id"].as_integer() == games[3])
            .values(status="running", locked_by="w1", locked_until=datetime.now(UTC))
        )
    stranger = store_game(
        session_factory,
        white=ParsedPlayer(Color.WHITE, "uuid-x", "Stranger", 1500, "win", Outcome.WIN, None),
    )
    queue_all_for_analysis(platform="chesscom", username="Stranger", engine=FakeEngine(),
                           session_factory=session_factory)  # fmt: skip

    assert cancel(session_factory, player_id) == 2

    assert jobs_by_game(session_factory) == {
        games[0]: ("queued", 100),  # on demand: kept
        games[1]: ("cancelled", 300),
        games[2]: ("cancelled", 300),
        games[3]: ("running", 300),  # finishes
        stranger: ("queued", 300),  # another player's
    }
    assert cancel(session_factory, player_id) == 0


def test_stop_withdraws_an_analyse_all_not_yet_expanded(session_factory, player_id) -> None:
    job_id, _ = request_analyse_all(session_factory, **ME)

    cancel(session_factory, player_id)

    with session_factory() as session:
        assert session.get(Job, job_id).status == "cancelled"


def test_analyse_all_after_stop_resumes(session_factory, games, player_id) -> None:
    first, _ = request_analyse_all(session_factory, **ME)
    expand(session_factory)
    analyse_game(games[3], engine=FakeEngine(), session_factory=session_factory)
    cancel(session_factory, player_id)

    second, created = request_analyse_all(session_factory, **ME)  # cancelled doesn't block
    assert created and second != first
    assert expand(session_factory) == [games[2], games[1], games[0]]  # analysed one skipped
    with session_factory() as session:
        queued = session.scalars(
            select(Job.payload["game_id"].as_integer()).where(
                Job.job_type == "analyse_game", Job.status == "queued", Job.priority == 300
            )
        ).all()
    assert sorted(queued) == games[:3]


# --------------------------------------------------------------- progress ---


def test_progress_counts_analysed_queued_and_running(session_factory, games, player_id) -> None:
    assert progress(session_factory, player_id).as_dict() == {
        "total_games": 4, "analysed_games": 0, "queued": 0, "running": 0, "expanding": False,
    }  # fmt: skip

    request_analyse_all(session_factory, **ME)
    assert progress(session_factory, player_id).expanding

    analyse_game(games[0], engine=FakeEngine(), session_factory=session_factory)
    expand(session_factory)
    with session_factory.begin() as session:
        jobs = JobRepository(session)
        first = jobs.claim("w1", lease=timedelta(minutes=1))
        assert first.job_type == "analyse_player"  # priority 100: expanded before bulk work
        assert jobs.claim("w2", lease=timedelta(minutes=1)).job_type == "analyse_game"

    # The analyse_player job is still running here (expand() above ran outside the queue).
    assert progress(session_factory, player_id).as_dict() == {
        "total_games": 4,
        "analysed_games": 1,
        "queued": 2,
        "running": 1,
        "expanding": True,
    }


# ---------------------------------------------------------------- handler ---


def test_handler_expands_the_request(session_factory, games) -> None:
    handle_analyse_player(ME, session_factory=session_factory,
                          engines=EngineHolder(FakeEngine))  # fmt: skip
    assert len(jobs_by_game(session_factory)) == 4


@pytest.mark.parametrize("payload", [{}, {"platform": "chesscom"}, {"username": "x"}])
def test_handler_rejects_a_malformed_payload(session_factory, payload) -> None:
    with pytest.raises(PermanentJobError, match="invalid"):
        handle_analyse_player(payload, session_factory=session_factory,
                              engines=EngineHolder(FakeEngine))  # fmt: skip


def test_handler_unknown_player_is_permanent(session_factory) -> None:
    with pytest.raises(PermanentJobError, match="not found"):
        handle_analyse_player({"platform": "chesscom", "username": "nobody"},
                              session_factory=session_factory,
                              engines=EngineHolder(FakeEngine))  # fmt: skip


def test_handler_engine_that_cannot_start_is_retryable(session_factory, games) -> None:
    def cannot_start():
        raise EngineError("cannot start engine 'stockfish': not found")

    with pytest.raises(RetryableJobError, match="engine failed"):
        handle_analyse_player(ME, session_factory=session_factory,
                              engines=EngineHolder(cannot_start))  # fmt: skip
    assert jobs_by_game(session_factory) == {}
