"""The sync use case end to end: a fake GameSource in front, the real migrated schema behind."""

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.ingestion.base import (
    ArchiveRef,
    PlayerNotFoundError,
    SourceGame,
    SourcePlayer,
)
from chess_analytics.services.sync import sync_player
from chess_analytics.storage.models.core import Game, Player, TrackedPlayer
from chess_analytics.storage.models.raw import ArchiveFetch, IngestionRun, RawGame
from tests.fakes import FakeGameSource
from tests.samples import chesscom_game

pytestmark = pytest.mark.integration

SEP = ArchiveRef(month=date(2026, 9, 1), url="https://example.test/2026/09")
OCT = ArchiveRef(month=date(2026, 10, 1), url="https://example.test/2026/10")


def game(game_id: str) -> SourceGame:
    return SourceGame(platform_game_id=game_id, payload={"uuid": game_id, "rules": "chess"})


@pytest.fixture
def source() -> FakeGameSource:
    return FakeGameSource(
        players={"someone": SourcePlayer("chesscom", "SomeOne", platform_player_id=77)},
        archives={"someone": [OCT, SEP]},  # deliberately unordered
        games={SEP.url: (game("g1"), game("g2")), OCT.url: (game("g3"),)},
    )


def count(session_factory: sessionmaker[Session], model: type) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def run_sync(source: FakeGameSource, session_factory, **kwargs):
    return sync_player("someone", source=source, session_factory=session_factory, **kwargs)


def test_first_sync_stores_every_game(source, session_factory) -> None:
    summary = run_sync(source, session_factory)

    assert summary.status == "succeeded"
    assert (summary.archives_checked, summary.games_seen, summary.new_raw_versions) == (2, 3, 3)
    assert source.fetched == [SEP.url, OCT.url]  # oldest first
    assert count(session_factory, RawGame) == 3
    assert count(session_factory, ArchiveFetch) == 2

    with session_factory() as session:
        run = session.get_one(IngestionRun, summary.run_id)
        assert (run.status, run.mode, run.new_raw_versions) == ("succeeded", "incremental", 3)
        assert run.finished_at is not None
        player = session.get_one(Player, summary.player_id)
        assert (player.username, player.platform_player_id) == ("SomeOne", 77)
        assert session.get_one(TrackedPlayer, player.id).last_synced_at is not None


def test_second_sync_downloads_nothing_unchanged(source, session_factory) -> None:
    run_sync(source, session_factory)
    second = run_sync(source, session_factory)

    assert second.status == "succeeded"
    assert (second.archives_not_modified, second.games_seen, second.new_raw_versions) == (2, 0, 0)
    assert count(session_factory, RawGame) == 3
    assert count(session_factory, Player) == 1  # same player, not a duplicate


def test_only_changed_archive_is_downloaded_again(source, session_factory) -> None:
    run_sync(source, session_factory)
    source.games[OCT.url] = (game("g3"), game("g4"))  # a new game finished in October

    summary = run_sync(source, session_factory)

    assert (summary.archives_not_modified, summary.games_seen) == (1, 2)
    assert summary.new_raw_versions == 1
    assert count(session_factory, RawGame) == 4


def test_full_refresh_ignores_etags_but_stores_no_duplicates(source, session_factory) -> None:
    run_sync(source, session_factory)
    summary = run_sync(source, session_factory, full_refresh=True)

    assert (summary.archives_not_modified, summary.games_seen) == (0, 3)
    assert summary.new_raw_versions == 0
    assert count(session_factory, RawGame) == 3


def test_source_failure_keeps_finished_archives_and_fails_the_run(source, session_factory) -> None:
    source.failing_urls = {OCT.url}

    summary = run_sync(source, session_factory)

    assert summary.status == "failed"
    assert "SourceUnavailableError" in summary.error
    assert count(session_factory, RawGame) == 2  # September was committed before October failed
    with session_factory() as session:
        run = session.get_one(IngestionRun, summary.run_id)
        assert (run.status, run.archives_checked) == ("failed", 1)
        tracked = session.get_one(TrackedPlayer, summary.player_id)
        assert tracked.last_synced_at is None  # a failed sync is not a sync


def test_retry_after_failure_resumes_where_it_stopped(source, session_factory) -> None:
    source.failing_urls = {OCT.url}
    run_sync(source, session_factory)
    source.failing_urls = set()
    source.fetched.clear()

    summary = run_sync(source, session_factory)

    assert summary.status == "succeeded"
    assert summary.archives_not_modified == 1  # September: 304, not downloaded again
    assert summary.new_raw_versions == 1  # only October's game is new
    assert count(session_factory, RawGame) == 3


def test_unknown_player_writes_nothing(source, session_factory) -> None:
    with pytest.raises(PlayerNotFoundError):
        sync_player("ghost", source=source, session_factory=session_factory)
    assert count(session_factory, IngestionRun) == 0
    assert count(session_factory, Player) == 0


def test_sync_processes_new_games_into_core(session_factory) -> None:
    valid = SourceGame(platform_game_id="game-1", payload=chesscom_game(uuid="game-1"))
    source = FakeGameSource(
        players={"someone": SourcePlayer("chesscom", "SomeOne", platform_player_id=77)},
        archives={"someone": [OCT]},
        games={OCT.url: (valid, game("not-a-real-game"))},  # second payload is malformed
    )

    summary = run_sync(source, session_factory)

    assert (summary.games_processed, summary.games_rejected) == (1, 1)
    assert count(session_factory, Game) == 1
    with session_factory() as session:
        run = session.get_one(IngestionRun, summary.run_id)
        assert (run.games_processed, run.games_failed) == (1, 1)

    nothing_new = run_sync(source, session_factory)
    assert (nothing_new.games_processed, nothing_new.games_rejected) == (0, 0)


def test_sync_can_skip_processing(source, session_factory) -> None:
    summary = run_sync(source, session_factory, process=False)
    assert (summary.games_processed, summary.games_rejected) == (0, 0)
