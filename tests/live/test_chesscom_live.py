"""Smoke test against the real Chess.com API. Never runs in CI.

    uv run pytest -m live
    CHESS_LIVE_USERNAME=someone uv run pytest -m live   # a different public account

It checks only shapes our code relies on and asserts nothing about a player's games,
so it keeps passing as new games are played.
"""

import os

import pytest

from chess_analytics.config import Settings
from chess_analytics.ingestion.base import PlayerNotFoundError
from chess_analytics.ingestion.chesscom.client import ChessComClient

pytestmark = pytest.mark.live

USERNAME = os.environ.get("CHESS_LIVE_USERNAME", "adarsxh1")


@pytest.fixture(scope="module")
def client():
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://unused@localhost/x")
    with ChessComClient.from_settings(settings) as client:
        yield client


def test_profile_archives_and_etag_round_trip(client: ChessComClient) -> None:
    player = client.get_player(USERNAME)
    assert player.username.lower() == USERNAME.lower()
    assert isinstance(player.platform_player_id, int)

    archives = client.list_archives(USERNAME)
    assert archives, "expected at least one monthly archive"

    latest = client.fetch_archive(archives[-1])
    assert latest.http_status == 200 and latest.etag
    assert all(game.platform_game_id for game in latest.games)

    again = client.fetch_archive(archives[-1], etag=latest.etag)
    # 304 if nothing changed in between; 200 if a game finished during the test.
    assert again.http_status in (200, 304)


def test_unknown_player(client: ChessComClient) -> None:
    with pytest.raises(PlayerNotFoundError):
        client.get_player("no-such-user-x9q2z7k1")
