"""Chess.com client against a mocked HTTP transport. No network: every response here
is synthetic, built from the shapes verified against real Chess.com data in Phase 2."""

from collections.abc import Callable
from datetime import date

import httpx2
import pytest

from chess_analytics.config import Settings
from chess_analytics.ingestion.base import (
    ArchiveRef,
    PlayerNotFoundError,
    SourceError,
    SourceUnavailableError,
)
from chess_analytics.ingestion.chesscom.client import MAX_ATTEMPTS, ChessComClient

BASE = "https://api.chess.test/pub"
OCT_URL = f"{BASE}/player/someone/games/2026/10"
OCT = ArchiveRef(month=date(2026, 10, 1), url=OCT_URL)

Handler = Callable[[httpx2.Request], httpx2.Response]


class Recorder:
    """Builds a client whose transport answers with `handler` and remembers requests."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.sleeps: list[float] = []

    def client(self, handler: Handler) -> ChessComClient:
        def record(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            return handler(request)

        settings = Settings(
            _env_file=None,
            database_url="postgresql+psycopg://u:p@localhost/db",
            chesscom_base_url=BASE,
            chesscom_user_agent="test-agent/1.0 (+https://example.test)",
        )
        return ChessComClient.from_settings(
            settings,
            transport=httpx2.MockTransport(record),
            sleep=self.sleeps.append,  # record waits instead of really sleeping
        )


@pytest.fixture
def rec() -> Recorder:
    return Recorder()


def game(uuid: str) -> dict:
    return {"uuid": uuid, "url": f"https://www.chess.test/game/live/{uuid}", "rules": "chess"}


# ----------------------------------------------------------------- profiles ---


def test_get_player_maps_profile_and_identifies_itself(rec: Recorder) -> None:
    client = rec.client(
        lambda r: httpx2.Response(200, json={"username": "Someone", "player_id": 42})
    )

    player = client.get_player("SomeOne")

    assert (player.platform, player.username, player.platform_player_id) == (
        "chesscom",
        "Someone",
        42,
    )
    request = rec.requests[0]
    assert str(request.url) == f"{BASE}/player/someone"  # lower-cased
    assert request.headers["user-agent"] == "test-agent/1.0 (+https://example.test)"


def test_unknown_player_is_not_found(rec: Recorder) -> None:
    client = rec.client(lambda r: httpx2.Response(404, json={"message": "not found"}))
    with pytest.raises(PlayerNotFoundError):
        client.get_player("ghost")


@pytest.mark.parametrize("username", ["../admin", "a/b", "", "name with space"])
def test_impossible_usernames_never_reach_the_network(rec: Recorder, username: str) -> None:
    client = rec.client(lambda r: httpx2.Response(200, json={}))
    with pytest.raises(PlayerNotFoundError):
        client.get_player(username)
    assert rec.requests == []


# ----------------------------------------------------------------- archives ---


def test_list_archives_parses_months_oldest_first(rec: Recorder) -> None:
    urls = [f"{BASE}/player/someone/games/2026/10", f"{BASE}/player/someone/games/2025/03"]
    client = rec.client(lambda r: httpx2.Response(200, json={"archives": urls}))

    archives = client.list_archives("someone")

    assert [a.month for a in archives] == [date(2025, 3, 1), date(2026, 10, 1)]
    assert archives[1].url == urls[0]


def test_fetch_archive_returns_games_with_their_etag(rec: Recorder) -> None:
    body = {"games": [game("g-1"), game("g-2")]}
    client = rec.client(lambda r: httpx2.Response(200, json=body, headers={"ETag": 'W/"v1"'}))

    result = client.fetch_archive(OCT)

    assert not result.not_modified
    assert result.etag == 'W/"v1"'
    assert [g.platform_game_id for g in result.games] == ["g-1", "g-2"]
    assert result.games[0].payload == body["games"][0]  # untouched payload
    assert "if-none-match" not in rec.requests[0].headers


def test_unchanged_archive_returns_not_modified(rec: Recorder) -> None:
    client = rec.client(lambda r: httpx2.Response(304))

    result = client.fetch_archive(OCT, etag='W/"v1"')

    assert rec.requests[0].headers["if-none-match"] == 'W/"v1"'
    assert result.not_modified and result.games == ()
    assert result.etag == 'W/"v1"'  # kept when the 304 omits it


def test_game_without_uuid_is_skipped(rec: Recorder) -> None:
    body = {"games": [game("g-1"), {"url": "no-uuid"}]}
    client = rec.client(lambda r: httpx2.Response(200, json=body))
    assert [g.platform_game_id for g in client.fetch_archive(OCT).games] == ["g-1"]


# ------------------------------------------------------------------ retries ---


def responses(*items: httpx2.Response | Exception) -> Handler:
    """A handler that answers with each item in turn (raising it if it's an exception)."""
    queue = list(items)

    def handler(request: httpx2.Request) -> httpx2.Response:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def test_server_error_is_retried_until_success(rec: Recorder) -> None:
    client = rec.client(responses(httpx2.Response(503), httpx2.Response(200, json={"games": []})))
    assert client.fetch_archive(OCT).games == ()
    assert len(rec.requests) == 2
    assert len(rec.sleeps) == 1


def test_rate_limit_honours_retry_after(rec: Recorder) -> None:
    client = rec.client(
        responses(
            httpx2.Response(429, headers={"Retry-After": "7"}),
            httpx2.Response(200, json={"games": []}),
        )
    )
    client.fetch_archive(OCT)
    assert rec.sleeps == [7.0]


def test_network_errors_are_retried(rec: Recorder) -> None:
    client = rec.client(
        responses(httpx2.ConnectError("boom"), httpx2.Response(200, json={"games": []}))
    )
    client.fetch_archive(OCT)
    assert len(rec.requests) == 2


def test_persistent_server_errors_become_unavailable(rec: Recorder) -> None:
    client = rec.client(lambda r: httpx2.Response(500))
    with pytest.raises(SourceUnavailableError, match="500"):
        client.fetch_archive(OCT)
    assert len(rec.requests) == MAX_ATTEMPTS


def test_persistent_network_errors_become_unavailable(rec: Recorder) -> None:
    def always_timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    client = rec.client(always_timeout)
    with pytest.raises(SourceUnavailableError, match="ReadTimeout"):
        client.fetch_archive(OCT)
    assert len(rec.requests) == MAX_ATTEMPTS


def test_client_errors_are_not_retried(rec: Recorder) -> None:
    client = rec.client(lambda r: httpx2.Response(400))
    with pytest.raises(SourceError, match="400"):
        client.fetch_archive(OCT)
    assert len(rec.requests) == 1


# ------------------------------------------------------- unexpected responses ---


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text="<html>maintenance</html>"),
        httpx2.Response(200, json=["not", "an", "object"]),
        httpx2.Response(200, json={"no_games_key": []}),
        httpx2.Response(200, json={"games": ["not-a-game"]}),
    ],
)
def test_malformed_archive_responses_raise_source_error(
    rec: Recorder, response: httpx2.Response
) -> None:
    client = rec.client(lambda r: response)
    with pytest.raises(SourceError):
        client.fetch_archive(OCT)
