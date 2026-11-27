"""The dashboard against the real API of the e2e stack: the contract check.

test_app.py uses a hand-written fake API; this runs the same pages against the backend's
real responses (the stub player, synced and analysed by the real worker and Stockfish).
    uv run pytest -m e2e      (with the e2e stack running; see tests/e2e/compose.yaml)
"""

import os
from pathlib import Path

import httpx2
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.e2e

API = os.environ.get("CHESS_E2E_API_URL", "http://127.0.0.1:18000")
APP = str(Path(__file__).resolve().parents[1] / "app.py")
PLAYER = "e2e_player"  # synced by the backend's e2e tests, which run first in CI


@pytest.fixture(autouse=True)
def real_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHESS_DASHBOARD_API_URL", API)
    st.cache_resource.clear()
    st.cache_data.clear()


def test_overview_page_renders_real_responses() -> None:
    app = AppTest.from_file(APP, default_timeout=30)
    app.session_state["username"] = PLAYER
    app.run()

    assert not app.exception and not app.error and not app.warning
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Games"] == "2"
    assert "cover 2 of 2 games" in app.caption[0].value
    assert len(app.get("vega_lite_chart")) == 3


def openings_script() -> None:
    from dashboard import openings_page

    openings_page.render()


def accuracy_script() -> None:
    from dashboard import accuracy_page

    accuracy_page.render()


def test_openings_and_accuracy_pages_render_real_responses() -> None:
    openings = AppTest.from_function(openings_script, default_timeout=30)
    openings.session_state["username"] = PLAYER
    openings.run()
    assert not openings.exception and not openings.error and not openings.warning
    assert len(openings.dataframe) >= 1

    accuracy = AppTest.from_function(accuracy_script, default_timeout=30)
    accuracy.session_state["username"] = PLAYER
    accuracy.run()
    assert not accuracy.exception and not accuracy.error and not accuracy.warning
    assert "Covers 2 of 2 games" in accuracy.caption[0].value
    assert len(accuracy.get("vega_lite_chart")) == 1


def games_script() -> None:
    from dashboard import games_page

    games_page.render()


def game_script() -> None:
    from dashboard import game_page

    game_page.render()


def test_games_and_game_pages_render_real_responses() -> None:
    games = AppTest.from_function(games_script, default_timeout=30)
    games.session_state["username"] = PLAYER
    games.run()
    assert not games.exception and not games.error
    listing = games.dataframe[0].value
    assert len(listing) == 2 and listing["accuracy"].notna().all()  # both analysed

    newest = int(httpx2.get(f"{API}/api/v1/players/{PLAYER}/games").json()["items"][0]["game_id"])
    game = AppTest.from_function(game_script, default_timeout=30)
    game.session_state["game_id"] = newest
    game.run()
    assert not game.exception and not game.error
    assert [m.label for m in game.metric] == ["White accuracy", "Black accuracy"]
    assert len(game.get("vega_lite_chart")) == 1
    assert game.dataframe[0].value["san"].iloc[-1].startswith("Qxf7#")
