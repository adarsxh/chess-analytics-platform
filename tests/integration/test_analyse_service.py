"""analyse_game against a real database, with a fake engine (scripted, call-recording)."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.classification import moves as classifier
from chess_analytics.engine.base import EngineError, EngineSettings
from chess_analytics.services.analyse import (
    GameNotFoundError,
    Outcome,
    analyse_game,
    latest_games_needing_analysis,
)
from chess_analytics.storage.models.analysis import (
    AnalysisConfig,
    GameAnalysis,
    MoveClassification,
    PositionEvaluation,
)
from chess_analytics.storage.models.core import Game
from chess_analytics.storage.repositories.core import CoreRepository
from tests.fakes import FakeEngine

from .factories import make_parsed_game, make_raw_game, parsed_moves

pytestmark = pytest.mark.integration

# 1.f3 e5 2.g4 Qh4#: four moves, the last one mates.
FOOLS_MATE = parsed_moves(("f3", "f2f3"), ("e5", "e7e5"), ("g4", "g2g4"), ("Qh4#", "d8h4"))


def store_game(session_factory: sessionmaker[Session], **overrides) -> int:
    with session_factory.begin() as session:
        raw = make_raw_game(session)
        parsed = make_parsed_game(moves=FOOLS_MATE, **overrides)
        return CoreRepository(session).save_game(parsed, raw_game_id=raw.id, parser_version=1)


def count(session_factory: sessionmaker[Session], model, *where) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def game_id(session_factory: sessionmaker[Session]) -> int:
    return store_game(session_factory)


def test_a_game_is_evaluated_classified_and_stored(session_factory, game_id: int) -> None:
    engine = FakeEngine()

    summary = analyse_game(game_id, engine=engine, session_factory=session_factory)

    assert (summary.outcome, summary.positions) == (Outcome.ANALYSED, 5)
    assert len(engine.analysed) == 4  # the final checkmate is not searched
    with session_factory() as session:
        analysis = session.get(GameAnalysis, summary.analysis_id)
        assert (analysis.status, analysis.error) == ("succeeded", None)
        assert analysis.started_at <= analysis.finished_at
        assert analysis.moves_hash == session.get(Game, game_id).moves_hash
        config = session.get(AnalysisConfig, summary.config_id)
        assert (config.engine_name, config.engine_version, config.nodes_per_position) == (
            "fake", "1", 1000,
        )  # fmt: skip
        evaluations = session.scalars(
            select(PositionEvaluation)
            .where(PositionEvaluation.game_analysis_id == analysis.id)
            .order_by(PositionEvaluation.ply)
        ).all()
        assert [e.ply for e in evaluations] == [0, 1, 2, 3, 4]
        mate = evaluations[-1]
        assert (mate.mate_in, mate.score_cp, mate.best_move_uci, mate.pv_uci) == (
            0,
            None,
            None,
            None,
        )
        labels = session.scalars(
            select(MoveClassification.label)
            .where(MoveClassification.game_analysis_id == analysis.id)
            .order_by(MoveClassification.ply)
        ).all()
        assert len(labels) == 4 and set(labels) <= {label.value for label in classifier.Label}


def test_a_second_run_does_nothing(session_factory, game_id: int) -> None:
    first = analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)
    engine = FakeEngine()

    second = analyse_game(game_id, engine=engine, session_factory=session_factory)

    assert second.outcome == Outcome.UP_TO_DATE and engine.analysed == []
    assert second.analysis_id == first.analysis_id
    assert count(session_factory, PositionEvaluation) == 5  # no duplicates


def test_a_new_classifier_relabels_without_the_engine(
    session_factory, game_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)
    monkeypatch.setattr(classifier, "CLASSIFIER_VERSION", 2)
    engine = FakeEngine()

    second = analyse_game(game_id, engine=engine, session_factory=session_factory)

    assert second.outcome == Outcome.RECLASSIFIED and engine.analysed == []
    by_version = MoveClassification.game_analysis_id == first.analysis_id
    assert count(session_factory, MoveClassification, by_version) == 8  # v1 kept, v2 added


def test_new_engine_settings_add_an_analysis_and_keep_the_old_one(
    session_factory, game_id: int
) -> None:
    first = analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)
    stronger = FakeEngine(settings=EngineSettings(nodes_per_position=5000))

    second = analyse_game(game_id, engine=stronger, session_factory=session_factory)

    assert second.outcome == Outcome.ANALYSED
    assert second.config_id != first.config_id and second.analysis_id != first.analysis_id
    assert count(session_factory, GameAnalysis, GameAnalysis.game_id == game_id) == 2


def test_identical_settings_share_one_config(session_factory) -> None:
    games = [store_game(session_factory) for _ in range(2)]
    summaries = [
        analyse_game(g, engine=FakeEngine(), session_factory=session_factory) for g in games
    ]
    assert summaries[0].config_id == summaries[1].config_id
    assert count(session_factory, AnalysisConfig) == 1


def test_a_game_whose_moves_changed_is_reanalysed_in_place(session_factory) -> None:
    platform_game_id = "changing-game"
    game_id = store_game(session_factory, platform_game_id=platform_game_id, moves_hash="v1")
    first = analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)
    # The platform corrected the game: re-processing gives it new moves and a new hash.
    shorter = parsed_moves(("e4", "e2e4"), ("e5", "e7e5"))
    with session_factory.begin() as session:
        raw = make_raw_game(session)
        parsed = make_parsed_game(platform_game_id=platform_game_id, moves=shorter, moves_hash="v2")
        assert (
            CoreRepository(session).save_game(parsed, raw_game_id=raw.id, parser_version=1)
            == game_id
        )

    second = analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)

    assert second.outcome == Outcome.ANALYSED and second.analysis_id == first.analysis_id
    assert count(session_factory, PositionEvaluation) == 3  # old evaluations replaced
    assert count(session_factory, MoveClassification) == 2  # and their labels
    with session_factory() as session:
        assert session.get(GameAnalysis, first.analysis_id).moves_hash == "v2"


def test_an_engine_failure_is_recorded_and_a_retry_succeeds(session_factory, game_id: int) -> None:
    with pytest.raises(EngineError):
        analyse_game(game_id, engine=FakeEngine(fail_after=2), session_factory=session_factory)

    with session_factory() as session:
        analysis = session.scalars(
            select(GameAnalysis).where(GameAnalysis.game_id == game_id)
        ).one()
        assert analysis.status == "failed" and "fake engine crashed" in analysis.error
    assert count(session_factory, PositionEvaluation) == 0  # nothing half-written

    retry = analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)
    assert retry.outcome == Outcome.ANALYSED and retry.analysis_id == analysis.id
    with session_factory() as session:
        analysis = session.get(GameAnalysis, retry.analysis_id)
        assert (analysis.status, analysis.error) == ("succeeded", None)


def test_an_unknown_game_is_an_error(session_factory) -> None:
    with pytest.raises(GameNotFoundError):
        analyse_game(999_999_999, engine=FakeEngine(), session_factory=session_factory)


def test_latest_games_needing_analysis(session_factory) -> None:
    base = datetime(2026, 10, 1, tzinfo=UTC)
    ids = [store_game(session_factory, end_time=base + timedelta(days=d)) for d in range(4)]
    engine = FakeEngine()
    analyse_game(ids[3], engine=engine, session_factory=session_factory)  # the newest one

    def latest(limit: int, username: str = "WhitePlayer", engine=engine) -> list[int] | None:
        return latest_games_needing_analysis(
            platform="chesscom", username=username, limit=limit,
            engine=engine, session_factory=session_factory,
        )  # fmt: skip

    assert latest(2) == [ids[2], ids[1]]  # newest first, analysed game skipped
    assert latest(10, username="blackplayer") == [ids[2], ids[1], ids[0]]  # either colour
    # Another config hasn't analysed anything yet.
    other = dataclasses.replace(engine, settings=EngineSettings(nodes_per_position=7))
    assert latest(10, engine=other) == list(reversed(ids))
    assert latest(5, username="nobody") is None


def test_stale_and_failed_analyses_still_need_analysis(session_factory) -> None:
    base = datetime(2026, 10, 1, tzinfo=UTC)
    stale, failed, done = (
        store_game(session_factory, end_time=base + timedelta(days=d)) for d in range(3)
    )
    engine = FakeEngine()
    for game in (stale, done):
        analyse_game(game, engine=engine, session_factory=session_factory)
    with pytest.raises(EngineError):
        analyse_game(failed, engine=FakeEngine(fail_after=0), session_factory=session_factory)
    with session_factory.begin() as session:  # the game's moves changed after its analysis
        session.get(Game, stale).moves_hash = "re-processed"

    needing = latest_games_needing_analysis(
        platform="chesscom", username="WhitePlayer", limit=10,
        engine=engine, session_factory=session_factory,
    )  # fmt: skip

    assert needing == [failed, stale]
