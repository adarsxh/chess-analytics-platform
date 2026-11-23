"""End-to-end: drive the running docker compose stack over HTTP, like a real client.

    API → ops.jobs → worker container → stub Chess.com → raw → core → API
                                      → Stockfish (real, in the image) → analysis → API

Needs the e2e stack running (see tests/e2e/compose.yaml); excluded from normal runs.
    uv run pytest -m e2e
"""

import os
import time

import httpx2
import pytest

pytestmark = pytest.mark.e2e

API = os.environ.get("CHESS_E2E_API_URL", "http://127.0.0.1:18000")  # tests/e2e/compose.yaml
DASHBOARD = os.environ.get("CHESS_E2E_DASHBOARD_URL", "http://127.0.0.1:18501")
PLAYER = "e2e_player"  # served by tests/e2e/stub_chesscom.py
JOB_TIMEOUT_SECONDS = 60


@pytest.fixture(scope="module")
def api() -> httpx2.Client:
    with httpx2.Client(base_url=API, timeout=10) as client:
        deadline = time.monotonic() + 60
        while client.get("/health/ready").status_code != 200:
            assert time.monotonic() < deadline, "API never became ready"
            time.sleep(1)
        yield client


def wait_for_job(api: httpx2.Client, job_id: int) -> dict:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while True:
        job = api.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        assert time.monotonic() < deadline, f"job still {job['status']} after timeout: {job}"
        time.sleep(0.5)


def request_sync(api: httpx2.Client, username: str) -> int:
    response = api.post(f"/api/v1/players/{username}/sync")
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def test_sync_through_the_whole_stack(api: httpx2.Client) -> None:
    job = wait_for_job(api, request_sync(api, PLAYER))
    assert job["status"] == "succeeded", job

    player = api.get(f"/api/v1/players/{PLAYER}").json()
    assert player["tracked"] and player["last_synced_at"]
    assert player["record"] == {"games": 2, "wins": 2, "losses": 0, "draws": 0}

    games = api.get(f"/api/v1/players/{PLAYER}/games").json()
    assert games["total"] == 2
    newest = games["items"][0]
    assert (newest["color"], newest["outcome"], newest["opponent_username"]) == (
        "white", "win", "opponent_two",
    )  # fmt: skip

    detail = api.get(f"/api/v1/games/{newest['game_id']}").json()
    assert [m["san"] for m in detail["moves"]][-1] == "Qxf7#"


def test_synced_games_are_analysed_with_stockfish(api: httpx2.Client) -> None:
    # The sync above queued both games for background analysis. Asking again returns that
    # job (moved ahead) or, if it already finished, a new one that finds nothing to do.
    games = api.get(f"/api/v1/players/{PLAYER}/games").json()["items"]
    for game in games:
        response = api.post(f"/api/v1/games/{game['game_id']}/analysis")
        assert response.status_code == 202, response.text
        job = wait_for_job(api, response.json()["job_id"])
        assert job["status"] == "succeeded", job

        analysis = api.get(f"/api/v1/games/{game['game_id']}/analysis").json()
        assert (analysis["status"], analysis["fresh"]) == ("succeeded", True)
        assert (analysis["engine"]["name"], analysis["engine"]["version"]) == ("stockfish", "19")
        assert analysis["engine"]["nodes_per_position"] == 20_000
        assert len(analysis["positions"]) == game["ply_count"] + 1
        assert len(analysis["moves"]) == game["ply_count"]

    newest = api.get(f"/api/v1/games/{games[0]['game_id']}/analysis").json()
    assert newest["positions"][-1]["mate_in"] == 0  # ends in Qxf7#: Black is mated
    assert newest["moves"][-1]["san"] == "Qxf7#"


def test_second_sync_is_idempotent(api: httpx2.Client) -> None:
    job = wait_for_job(api, request_sync(api, PLAYER))  # stub answers 304 this time
    assert job["status"] == "succeeded", job
    assert api.get(f"/api/v1/players/{PLAYER}/games").json()["total"] == 2  # no duplicates


def test_unknown_player_fails_permanently_without_retries(api: httpx2.Client) -> None:
    job = wait_for_job(api, request_sync(api, "no_such_player"))
    assert (job["status"], job["attempts"]) == ("failed", 1)
    assert "PermanentJobError: player not found" in job["last_error"]


def test_the_dashboard_is_up() -> None:
    # Its behaviour is tested with AppTest in frontend/tests; here: the image starts and serves.
    response = httpx2.get(f"{DASHBOARD}/_stcore/health", timeout=10)
    assert (response.status_code, response.text) == (200, "ok")
