"""API v1 endpoints over HTTP (FastAPI TestClient) against the real migrated schema.

The app's session factory is replaced by the test one, so everything an endpoint does
happens inside the test's transaction and is rolled back afterwards.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.api.app import create_app
from chess_analytics.api.dependencies import get_session_factory
from chess_analytics.domain.game import Color, GameResult, Outcome, ParsedPlayer, TimeClass
from chess_analytics.engine.base import EngineError
from chess_analytics.services.analyse import analyse_game
from chess_analytics.storage.models.core import Game
from chess_analytics.storage.repositories.core import CoreRepository
from chess_analytics.storage.repositories.players import PlayerRepository
from tests.fakes import FakeEngine

from .factories import make_parsed_game, make_raw_game, store_game

pytestmark = pytest.mark.integration

ME = ParsedPlayer(Color.WHITE, "uuid-me", "Adarsxh1", 1500, "win", Outcome.WIN, Decimal("88.5"))


@pytest.fixture
def client(make_settings, session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(make_settings())
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    with TestClient(app) as test_client:
        yield test_client


def add_game(session_factory, *, minutes_ago: int, me_white: bool = True, **overrides) -> int:
    """Store one processed game for the player "Adarsxh1" against a fresh opponent."""
    with session_factory.begin() as session:
        n = minutes_ago
        opponent = ParsedPlayer(Color.BLACK, f"uuid-opp-{n}", f"opp{n}", 1400 + n,
                                "resigned", Outcome.LOSS, None)  # fmt: skip
        white, black = (
            (ME, opponent)
            if me_white
            else (
                replace(opponent, color=Color.WHITE, result_code="win", outcome=Outcome.WIN),
                replace(ME, color=Color.BLACK, result_code="resigned", outcome=Outcome.LOSS),
            )
        )
        result = GameResult.WHITE_WIN
        parsed = make_parsed_game(
            white=white, black=black, result=result,
            end_time=datetime(2026, 11, 1, tzinfo=UTC) - timedelta(minutes=minutes_ago),
            **overrides,
        )  # fmt: skip
        raw = make_raw_game(session, platform_game_id=parsed.platform_game_id)
        return CoreRepository(session).save_game(parsed, raw_game_id=raw.id, parser_version=1)


def track(session_factory, username: str = "Adarsxh1") -> None:
    with session_factory.begin() as session:
        player = PlayerRepository(session).resolve_from_game(
            platform="chesscom", platform_uuid="uuid-me", username=username
        )
        PlayerRepository(session).mark_synced(player)


# ------------------------------------------------------------------- sync ---


def test_request_sync_returns_202_with_a_job(client: TestClient) -> None:
    response = client.post("/api/v1/players/Adarsxh1/sync")

    assert response.status_code == 202
    body = response.json()
    assert (body["status"], body["job_type"], body["created"]) == ("queued", "sync_player", True)
    assert body["payload"] == {"platform": "chesscom", "username": "Adarsxh1"}
    assert response.headers["location"] == f"/api/v1/jobs/{body['job_id']}"


def test_repeated_sync_request_returns_the_same_job(client: TestClient) -> None:
    first = client.post("/api/v1/players/adarsxh1/sync").json()
    again = client.post("/api/v1/players/ADARSXH1/sync").json()
    assert again["job_id"] == first["job_id"]
    assert again["created"] is False


@pytest.mark.parametrize(
    "url",
    ["/api/v1/players/bad%20name/sync", "/api/v1/players/x/sync?platform=lichess"],
)
def test_invalid_sync_requests_are_rejected(client: TestClient, url: str) -> None:
    assert client.post(url).status_code == 422


# ------------------------------------------------------------------- jobs ---


def test_job_status(client: TestClient) -> None:
    job_id = client.post("/api/v1/players/adarsxh1/sync").json()["job_id"]

    body = client.get(f"/api/v1/jobs/{job_id}").json()

    assert (body["job_id"], body["status"], body["attempts"], body["max_attempts"]) == (
        job_id, "queued", 0, 3,
    )  # fmt: skip
    assert "locked_by" not in body  # internals stay internal


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/jobs/999999999").status_code == 404


# ---------------------------------------------------------------- players ---


def test_unknown_player_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/players/nobody_here")
    assert response.status_code == 404
    assert "request a sync first" in response.json()["detail"]


def test_player_profile_and_record(client: TestClient, session_factory) -> None:
    add_game(session_factory, minutes_ago=1)
    add_game(session_factory, minutes_ago=2, me_white=False)
    track(session_factory)

    body = client.get("/api/v1/players/ADARSXH1").json()  # case-insensitive

    assert (body["username"], body["platform_uuid"], body["tracked"]) == (
        "Adarsxh1", "uuid-me", True,
    )  # fmt: skip
    assert body["last_synced_at"] is not None
    assert body["record"] == {"games": 2, "wins": 1, "losses": 1, "draws": 0}


# ------------------------------------------------------------------ games ---


def test_games_are_paginated_newest_first(client: TestClient, session_factory) -> None:
    ids = [add_game(session_factory, minutes_ago=n) for n in (3, 1, 2)]  # stored out of order

    page = client.get("/api/v1/players/adarsxh1/games?limit=2").json()
    rest = client.get("/api/v1/players/adarsxh1/games?limit=2&offset=2").json()

    newest_first = [ids[1], ids[2], ids[0]]
    assert [g["game_id"] for g in page["items"]] == newest_first[:2]
    assert [g["game_id"] for g in rest["items"]] == newest_first[2:]
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 0)


def test_game_summary_is_from_the_players_point_of_view(
    client: TestClient, session_factory
) -> None:
    add_game(session_factory, minutes_ago=5, me_white=False)

    game = client.get("/api/v1/players/adarsxh1/games").json()["items"][0]

    assert (game["color"], game["outcome"], game["result_code"]) == ("black", "loss", "resigned")
    assert (game["opponent_username"], game["opponent_rating_after"]) == ("opp5", 1405)
    assert game["accuracy"] == "88.50"


def test_games_can_be_filtered(client: TestClient, session_factory) -> None:
    add_game(session_factory, minutes_ago=1)  # rapid, win
    add_game(session_factory, minutes_ago=2, me_white=False)  # rapid, loss
    add_game(session_factory, minutes_ago=3, time_class=TimeClass.BLITZ, time_control="180")

    def total(query: str) -> int:
        return client.get(f"/api/v1/players/adarsxh1/games?{query}").json()["total"]

    assert total("time_class=blitz") == 1
    assert total("outcome=loss") == 1
    assert total("time_class=rapid&outcome=win") == 1


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "time_class=classical"])
def test_invalid_list_parameters_are_rejected(
    client: TestClient, session_factory, query: str
) -> None:
    add_game(session_factory, minutes_ago=1)
    assert client.get(f"/api/v1/players/adarsxh1/games?{query}").status_code == 422


def test_game_detail_with_moves(client: TestClient, session_factory) -> None:
    game_id = add_game(session_factory, minutes_ago=1)

    body = client.get(f"/api/v1/games/{game_id}").json()

    assert (body["game_id"], body["result"], body["ply_count"]) == (game_id, "white_win", 2)
    assert body["white"]["username"] == "Adarsxh1"
    assert body["black"]["outcome"] == "loss"
    assert [m["san"] for m in body["moves"]] == ["e4", "e5"]
    assert client.get(f"/api/v1/games/{game_id}?include_moves=false").json()["moves"] == []


def test_unknown_game_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/games/999999999").status_code == 404


# --------------------------------------------------------------- analysis ---


def test_request_analysis_returns_202_with_a_job(client: TestClient, session_factory) -> None:
    game_id = store_game(session_factory)

    response = client.post(f"/api/v1/games/{game_id}/analysis")
    again = client.post(f"/api/v1/games/{game_id}/analysis").json()

    assert response.status_code == 202
    body = response.json()
    assert (body["job_type"], body["payload"], body["created"]) == (
        "analyse_game", {"game_id": game_id}, True,
    )  # fmt: skip
    assert response.headers["location"] == f"/api/v1/jobs/{body['job_id']}"
    assert (again["job_id"], again["created"]) == (body["job_id"], False)


def test_analysis_of_an_unknown_game_is_404(client: TestClient) -> None:
    assert client.post("/api/v1/games/999999999/analysis").status_code == 404
    response = client.get("/api/v1/games/999999999/analysis")
    assert (response.status_code, response.json()["detail"]) == (404, "game 999999999 not found")


def test_a_game_never_analysed_is_404_with_a_hint(client: TestClient, session_factory) -> None:
    game_id = store_game(session_factory)
    response = client.get(f"/api/v1/games/{game_id}/analysis")
    assert response.status_code == 404
    assert "POST to this URL" in response.json()["detail"]


def test_analysis_with_evaluations_and_labels(client: TestClient, session_factory) -> None:
    game_id = store_game(session_factory)
    analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)

    body = client.get(f"/api/v1/games/{game_id}/analysis").json()

    assert (body["status"], body["fresh"], body["error"], body["classifier_version"]) == (
        "succeeded", True, None, 1,
    )  # fmt: skip
    assert (body["engine"]["name"], body["engine"]["nodes_per_position"]) == ("fake", 1000)
    assert [p["ply"] for p in body["positions"]] == [0, 1, 2, 3, 4]
    mate = body["positions"][-1]
    assert (mate["mate_in"], mate["score_cp"], mate["best_move_uci"], mate["pv_uci"]) == (
        0, None, None, [],
    )  # fmt: skip
    assert [(m["ply"], m["san"], m["uci"]) for m in body["moves"]] == [
        (1, "f3", "f2f3"), (2, "e5", "e7e5"), (3, "g4", "g2g4"), (4, "Qh4#", "d8h4"),
    ]  # fmt: skip
    labels = {"forced", "best", "excellent", "good", "inaccuracy", "mistake", "blunder"}
    assert all(m["label"] in labels and float(m["win_loss"]) >= 0 for m in body["moves"])


def test_a_failed_reanalysis_hides_rows_and_shows_the_error(
    client: TestClient, session_factory
) -> None:
    game_id = store_game(session_factory)
    analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)
    with session_factory.begin() as session:  # the platform corrected the game's moves
        session.get(Game, game_id).moves_hash = "re-processed"
    with pytest.raises(EngineError):
        analyse_game(game_id, engine=FakeEngine(fail_after=0), session_factory=session_factory)

    body = client.get(f"/api/v1/games/{game_id}/analysis").json()

    assert (body["status"], body["fresh"]) == ("failed", True)
    assert "fake engine crashed" in body["error"]
    assert (body["positions"], body["moves"], body["classifier_version"]) == ([], [], None)


def test_a_fresh_analysis_is_preferred_over_a_newer_failed_one(
    client: TestClient, session_factory
) -> None:
    game_id = store_game(session_factory)
    good = analyse_game(game_id, engine=FakeEngine(), session_factory=session_factory)
    other_config = FakeEngine(fail_after=0, settings=replace(FakeEngine().settings, hash_mb=1))
    with pytest.raises(EngineError):
        analyse_game(game_id, engine=other_config, session_factory=session_factory)

    body = client.get(f"/api/v1/games/{game_id}/analysis").json()

    assert (body["analysis_id"], body["status"]) == (good.analysis_id, "succeeded")


# ------------------------------------------------------------------ stats ---


def test_stats_overview(client: TestClient, session_factory) -> None:
    add_game(session_factory, minutes_ago=1)
    add_game(session_factory, minutes_ago=2, me_white=False)
    add_game(session_factory, minutes_ago=3, time_class=TimeClass.BLITZ)

    body = client.get("/api/v1/players/adarsxh1/stats/overview").json()
    rapid = client.get("/api/v1/players/adarsxh1/stats/overview?time_class=rapid").json()

    assert body["total"] == {"games": 3, "wins": 2, "draws": 0, "losses": 1,
                             "score_percent": "66.7"}  # fmt: skip
    assert set(body["by_time_class"]) == {"rapid", "blitz"}
    assert (body["by_color"]["white"]["games"], body["by_color"]["black"]["games"]) == (2, 1)
    assert rapid["total"]["games"] == 2


def test_stats_date_filters(client: TestClient, session_factory) -> None:
    add_game(session_factory, minutes_ago=60 * 24 * 3)  # 2026-10-29
    add_game(session_factory, minutes_ago=1)  # 2026-10-31

    def games(query: str) -> int:
        url = f"/api/v1/players/adarsxh1/stats/overview?{query}"
        return client.get(url).json()["total"]["games"]

    assert games("since=2026-10-30") == 1
    assert games("until=2026-10-29") == 1
    assert games("since=2026-10-29&until=2026-10-31") == 2


@pytest.mark.parametrize(
    "query", ["since=2026-11-02&until=2026-11-01", "time_class=classical", "since=yesterday"]
)
def test_invalid_stats_filters_are_422(client: TestClient, session_factory, query) -> None:
    add_game(session_factory, minutes_ago=1)
    assert client.get(f"/api/v1/players/adarsxh1/stats/overview?{query}").status_code == 422


@pytest.mark.parametrize("view", ["overview", "rating-history", "openings", "accuracy"])
def test_stats_of_an_unknown_player_are_404(client: TestClient, view: str) -> None:
    assert client.get(f"/api/v1/players/nobody_here/stats/{view}").status_code == 404


def test_stats_rating_history_and_openings(client: TestClient, session_factory) -> None:
    add_game(session_factory, minutes_ago=1, source_opening_name="Scotch Game 3...d6")
    add_game(session_factory, minutes_ago=2, source_opening_name="Scotch Game")

    history = client.get("/api/v1/players/adarsxh1/stats/rating-history").json()
    families = client.get("/api/v1/players/adarsxh1/stats/openings").json()
    names = client.get("/api/v1/players/adarsxh1/stats/openings?group_by=name&limit=1").json()

    assert history == [{"time_class": "rapid", "day": "2026-10-31", "rating": 1500, "games": 2}]
    assert [(o["opening"], o["color"], o["games"]) for o in families] == [
        ("Scotch Game", "white", 2)
    ]
    assert len(names) == 1


def test_stats_accuracy_with_coverage(client: TestClient, session_factory) -> None:
    analysed = add_game(session_factory, minutes_ago=1)
    add_game(session_factory, minutes_ago=2)
    analyse_game(analysed, engine=FakeEngine(), session_factory=session_factory)

    body = client.get("/api/v1/players/adarsxh1/stats/accuracy").json()

    assert body["coverage"] == {"analysed_games": 1, "total_games": 2, "percent": "50.0"}
    (game,) = body["games"]
    assert (game["game_id"], game["color"], game["moves"]) == (analysed, "white", 1)
    assert 0 <= float(game["accuracy"]) <= 100
    assert body["summary"]["accuracy"] == game["accuracy"]


def test_business_endpoints_are_versioned_health_is_not(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/health" in paths
    assert all(p.startswith("/api/v1/") for p in paths if not p.startswith("/health"))
