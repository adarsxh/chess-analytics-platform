"""Player and raw repositories against the real, migrated schema."""

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chess_analytics.storage.models.core import TrackedPlayer
from chess_analytics.storage.models.raw import ArchiveFetch, IngestionRun, RawGame
from chess_analytics.storage.repositories.players import PlayerRepository
from chess_analytics.storage.repositories.raw import RawRepository, content_hash

from .factories import make_player

pytestmark = pytest.mark.integration

OCT, NOV = date(2026, 10, 1), date(2026, 11, 1)


@pytest.fixture
def players(session: Session) -> PlayerRepository:
    return PlayerRepository(session)


@pytest.fixture
def raw(session: Session) -> RawRepository:
    return RawRepository(session)


@pytest.fixture
def run(session: Session, raw: RawRepository) -> IngestionRun:
    return raw.start_run(player_id=make_player(session).id, mode="incremental")


def fetch(raw: RawRepository, run: IngestionRun, month: date = OCT, **overrides) -> ArchiveFetch:
    values = {"url": f"https://example.test/{month}", "http_status": 200, "etag": None,
              "games_in_archive": 0} | overrides  # fmt: skip
    return raw.record_fetch(run, archive_month=month, **values)


# ----------------------------------------------------------------- players ---


def test_new_profile_creates_a_placeholder(players: PlayerRepository) -> None:
    player = players.resolve_from_profile(
        platform="chesscom", username="NewPerson", platform_player_id=7
    )
    assert player.id is not None
    assert (player.platform_uuid, player.username, player.platform_player_id) == (
        None, "NewPerson", 7,
    )  # fmt: skip


def test_same_profile_resolves_to_the_same_player(players: PlayerRepository) -> None:
    first = players.resolve_from_profile(platform="chesscom", username="A", platform_player_id=1)
    again = players.resolve_from_profile(platform="chesscom", username="a", platform_player_id=1)
    assert again.id == first.id


def test_rename_is_followed_by_numeric_id(players: PlayerRepository) -> None:
    before = players.resolve_from_profile(platform="chesscom", username="old", platform_player_id=9)
    after = players.resolve_from_profile(platform="chesscom", username="new", platform_player_id=9)
    assert after.id == before.id
    assert after.username == "new"


def test_known_player_found_by_username_gains_numeric_id(
    session: Session, players: PlayerRepository
) -> None:
    # e.g. first seen as an opponent in a game (uuid known), now synced via their profile.
    known = make_player(session, username="Opponent", platform_player_id=None)
    resolved = players.resolve_from_profile(
        platform="chesscom", username="opponent", platform_player_id=55
    )
    assert resolved.id == known.id
    assert resolved.platform_player_id == 55


def test_track_is_idempotent_and_records_sync(session: Session, players: PlayerRepository) -> None:
    player = make_player(session)
    players.track(player)
    players.track(player)
    players.mark_synced(player)

    tracked = session.scalars(select(TrackedPlayer)).all()
    assert len(tracked) == 1
    assert tracked[0].last_synced_at is not None


# --------------------------------------------------------------------- runs ---


def test_run_lifecycle(raw: RawRepository, run: IngestionRun) -> None:
    assert run.status == "running"
    raw.finish_run(run)
    assert (run.status, run.error) == ("succeeded", None)
    assert run.finished_at is not None


def test_failed_run_keeps_its_error(raw: RawRepository, run: IngestionRun) -> None:
    raw.finish_run(run, error="SourceUnavailableError: 503 after 4 attempts")
    assert run.status == "failed"
    assert "503" in run.error


# ------------------------------------------------------------------ etags ---


def test_latest_etag_per_month_ignores_failed_fetches(
    raw: RawRepository, run: IngestionRun
) -> None:
    fetch(raw, run, OCT, etag='"oct-1"')
    fetch(raw, run, OCT, http_status=304, etag='"oct-2"', games_in_archive=None)
    fetch(raw, run, OCT, http_status=500, etag='"broken"')  # failure: must not win
    fetch(raw, run, NOV, etag='"nov-1"')
    fetch(raw, run, date(2026, 9, 1), etag=None)  # no etag: not returned

    assert raw.latest_etags(run.player_id) == {OCT: '"oct-2"', NOV: '"nov-1"'}


def test_etags_are_per_player(session: Session, raw: RawRepository, run: IngestionRun) -> None:
    fetch(raw, run, OCT, etag='"mine"')
    other_run = raw.start_run(player_id=make_player(session).id, mode="incremental")
    assert raw.latest_etags(other_run.player_id) == {}


# -------------------------------------------------------------- raw games ---


def test_storing_games_is_idempotent(
    session: Session, raw: RawRepository, run: IngestionRun
) -> None:
    games = {"g1": {"uuid": "g1", "end_time": 1}, "g2": {"uuid": "g2", "end_time": 2}}

    first_fetch = fetch(raw, run)
    first = raw.store_games(first_fetch, platform="chesscom", games=games)
    second = raw.store_games(fetch(raw, run), platform="chesscom", games=games)

    assert (first.new_versions, first.seen_again) == (2, 0)
    assert (second.new_versions, second.seen_again) == (0, 2)
    assert first_fetch.new_versions_stored == 2
    assert session.scalar(select(func.count()).select_from(RawGame)) == 2


def test_changed_game_becomes_a_new_version(
    session: Session, raw: RawRepository, run: IngestionRun
) -> None:
    raw.store_games(fetch(raw, run), platform="chesscom", games={"g1": {"uuid": "g1"}})
    changed = {"g1": {"uuid": "g1", "accuracies": {"white": 80.1, "black": 70.4}}}
    result = raw.store_games(fetch(raw, run), platform="chesscom", games=changed)

    assert result.new_versions == 1
    versions = session.scalars(select(RawGame).where(RawGame.platform_game_id == "g1")).all()
    assert len(versions) == 2


def test_stored_payload_and_lineage(
    session: Session, raw: RawRepository, run: IngestionRun
) -> None:
    payload = {"uuid": "g1", "pgn": "1. e4 e5", "white": {"username": "x", "rating": 1500}}
    archive_fetch = fetch(raw, run)
    raw.store_games(archive_fetch, platform="chesscom", games={"g1": payload})

    stored = session.scalar(select(RawGame).where(RawGame.platform_game_id == "g1"))
    assert stored.payload == payload
    assert stored.content_hash == content_hash(payload)
    assert stored.source_fetch_id == archive_fetch.id


def test_empty_archive_stores_nothing(raw: RawRepository, run: IngestionRun) -> None:
    archive_fetch = fetch(raw, run)
    assert raw.store_games(archive_fetch, platform="chesscom", games={}).new_versions == 0
    assert archive_fetch.new_versions_stored == 0
