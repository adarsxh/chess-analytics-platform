"""The GameSource contract: its types reject invalid states, and it can be implemented
without any network code (the fake here is reused by the ingestion service tests)."""

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from chess_analytics.ingestion.base import (
    ArchiveFetchResult,
    ArchiveRef,
    GameSource,
    PlayerNotFoundError,
    SourceError,
    SourceGame,
    SourcePlayer,
)
from tests.fakes import FakeGameSource

OCT = ArchiveRef(month=date(2026, 10, 1), url="https://example.test/2026/10")


def test_archive_month_must_be_first_day() -> None:
    with pytest.raises(ValueError, match="first of the month"):
        ArchiveRef(month=date(2026, 10, 15), url="u")


def test_game_needs_an_id() -> None:
    with pytest.raises(ValueError, match="platform_game_id"):
        SourceGame(platform_game_id="", payload={})


def test_not_modified_result_cannot_carry_games() -> None:
    game = SourceGame(platform_game_id="g1", payload={})
    with pytest.raises(ValueError, match="not-modified"):
        ArchiveFetchResult(archive=OCT, http_status=304, etag='"e"', games=(game,))


def test_types_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        OCT.url = "changed"  # type: ignore[misc]


def test_payload_is_kept_out_of_repr() -> None:
    game = SourceGame(platform_game_id="g1", payload={"pgn": "1. e4 e5 ..."})
    assert "pgn" not in repr(game)


def test_errors_share_one_base_class() -> None:
    # The service can catch SourceError without knowing which platform raised it.
    assert issubclass(PlayerNotFoundError, SourceError)


# --------------------------------------------------- the fake implementation ---


@pytest.fixture
def source() -> GameSource:
    game = SourceGame(platform_game_id="g1", payload={"uuid": "g1"})
    return FakeGameSource(
        players={"adarsxh1": SourcePlayer("chesscom", "Adarsxh1", platform_player_id=1)},
        archives={"adarsxh1": [OCT]},
        games={OCT.url: (game,)},
    )


def test_unknown_player_raises(source: GameSource) -> None:
    with pytest.raises(PlayerNotFoundError):
        source.get_player("nobody")


def test_conditional_fetch_flow(source: GameSource) -> None:
    # The sequence ingestion relies on: full fetch, then 304 while nothing changed.
    archive = source.list_archives("ADARSXH1")[0]

    first = source.fetch_archive(archive)
    assert not first.not_modified and len(first.games) == 1

    again = source.fetch_archive(archive, etag=first.etag)
    assert again.not_modified and again.games == ()
