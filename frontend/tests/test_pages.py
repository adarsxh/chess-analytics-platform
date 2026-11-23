"""Games page, game page, and the sync / analyse buttons, via AppTest with a fake API."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard import session
from dashboard.api import NotFound

APP = str(Path(__file__).resolve().parents[1] / "app.py")
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
SIDE = {"rating_after": 750, "outcome": "win", "result_code": "win", "accuracy": None}
GAME = {
    "game_id": 5,
    "url": "https://www.chess.com/game/live/5",
    "time_class": "rapid",
    "time_control": "600",
    "end_time": "2026-11-01T10:00:00Z",
    "result": "white_win",
    "termination": "white won by checkmate",
    "opening_name": "Scotch Game",
    "initial_fen": START,
    "white": {**SIDE, "username": "me"},
    "black": {**SIDE, "username": "them", "outcome": "loss"},
    "moves": [],
}
ANALYSIS = {
    "status": "succeeded",
    "fresh": True,
    "error": None,
    "classifier_version": 1,
    "engine": {"name": "stockfish", "version": "19", "nodes_per_position": 300000},
    "accuracy": {"white": "91.2", "black": "40.5"},
    "positions": [
        {"ply": 0, "score_cp": 20, "mate_in": None},
        {"ply": 1, "score_cp": 30, "mate_in": None},
        {"ply": 2, "score_cp": 400, "mate_in": None},
    ],
    "moves": [
        {
            "ply": 1,
            "color": "white",
            "san": "e4",
            "uci": "e2e4",
            "label": "best",
            "win_loss": "0.00",
        },
        {
            "ply": 2,
            "color": "black",
            "san": "f6",
            "uci": "f7f6",
            "label": "blunder",
            "win_loss": "35.10",
        },
    ],
}
LISTING = {
    "total": 1,
    "items": [
        {
            "game_id": 5,
            "end_time": "2026-11-01T10:00:00Z",
            "time_class": "rapid",
            "time_control": "600",
            "color": "white",
            "outcome": "win",
            "rating_after": 750,
            "opening_name": "Scotch Game",
            "opponent_username": "them",
            "opponent_rating_after": 700,
        }
    ],
}
QUALITY = {"coverage": {}, "summary": {}, "games": [{"game_id": 5, "accuracy": "91.2"}]}


class FakeApi:
    def __init__(self) -> None:
        self.analysis_result: dict | None = ANALYSIS
        self.job_status = "running"
        self.posted: list[str] = []

    def games(self, username, **kwargs):
        return LISTING

    def stats(self, username, view, params):
        return QUALITY

    def game(self, game_id):
        if game_id != 5:
            raise NotFound("no such game")
        return GAME

    def analysis(self, game_id):
        if self.analysis_result is None:
            raise NotFound("not analysed")
        return self.analysis_result

    def request_analysis(self, game_id):
        self.posted.append(f"analysis:{game_id}")
        return {"job_id": 77}

    def request_sync(self, username):
        self.posted.append(f"sync:{username}")
        return {"job_id": 88}

    def job(self, job_id):
        return {"job_id": job_id, "status": self.job_status, "attempts": 1, "max_attempts": 3,
                "last_error": "boom" if self.job_status == "failed" else None}  # fmt: skip


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(session, "client", lambda: fake)
    st.cache_data.clear()
    return fake


def games_script() -> None:
    from dashboard import games_page

    games_page.render()


def game_script() -> None:
    from dashboard import game_page

    game_page.render()


def page(script, **state) -> AppTest:
    app = AppTest.from_function(script, default_timeout=30)
    for key, value in state.items():
        app.session_state[key] = value
    return app.run()


def test_games_page_lists_games_with_our_accuracy(api: FakeApi) -> None:
    app = page(games_script, username="me")
    assert not app.exception
    assert app.header[0].value == "Games of me"
    table = app.dataframe[0].value
    assert table["opponent"].tolist() == ["them (700)"]
    assert table["accuracy"].tolist() == [91.2]


def test_game_page_shows_the_analysis(api: FakeApi) -> None:
    app = page(game_script, game_id=5)
    assert not app.exception
    assert app.header[0].value == "me (750) 1–0 them (750)"
    assert {m.label: m.value for m in app.metric} == {
        "White accuracy": "91.2 %", "Black accuracy": "40.5 %",
    }  # fmt: skip
    assert len(app.get("vega_lite_chart")) == 1
    assert app.dataframe[0].value["san"].tolist() == ["e4★", "f6??"]


def test_game_page_without_a_game(api: FakeApi) -> None:
    assert "Open a game from the **Games** page" in page(game_script).info[0].value
    assert "doesn't exist" in page(game_script, game_id=6).warning[0].value


def test_an_unanalysed_game_can_be_analysed_and_followed(api: FakeApi) -> None:
    api.analysis_result = None
    app = page(game_script, game_id=5)
    assert "hasn't been analysed yet" in app.info[0].value

    app.button[0].click().run()

    assert api.posted == ["analysis:5"]
    assert app.session_state["analysis_job:5"] == 77
    assert any("Analysis: running (attempt 1 of 3)" in i.value for i in app.info)

    api.job_status, api.analysis_result = "succeeded", ANALYSIS
    app.run()  # the next poll sees the finished job and reloads the page
    assert "analysis_job:5" not in app.session_state
    assert app.success[0].value == "Analysis: done."
    assert len(app.metric) == 2


@pytest.mark.parametrize(
    ("analysis", "message", "button"),
    [
        ({**ANALYSIS, "status": "failed", "error": "engine crashed"}, "failed: engine crashed",
         "Try again"),
        ({**ANALYSIS, "fresh": False}, "changed since it was analysed", "Analyse again"),
        ({**ANALYSIS, "status": "running"}, "is running", "Refresh"),
    ],
)  # fmt: skip
def test_unusable_analyses_explain_themselves(api: FakeApi, analysis, message, button) -> None:
    api.analysis_result = analysis
    app = page(game_script, game_id=5)
    assert message in app.info[0].value
    assert app.button[0].label == button
    assert len(app.metric) == 0  # no analysis shown


def test_sync_button_queues_and_follows_a_sync(api: FakeApi) -> None:
    app = AppTest.from_file(APP, default_timeout=30)
    app.session_state["username"] = "me"
    app.run()
    sync = next(b for b in app.sidebar.button if b.label == "Sync from Chess.com")

    sync.click().run()
    assert api.posted == ["sync:me"]
    assert any("Sync: running" in i.value for i in app.sidebar.info)

    api.job_status = "failed"
    app.run()
    assert app.sidebar.error[0].value == "Sync failed: boom"
