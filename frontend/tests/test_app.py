"""The real Streamlit script, run in-process by AppTest against a fake API."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard import session
from dashboard.api import NotFound

APP = str(Path(__file__).resolve().parents[1] / "app.py")
RECORD = {"games": 2, "wins": 1, "draws": 0, "losses": 1, "score_percent": "50.0"}
STATS = {
    "overview": {"total": RECORD, "by_time_class": {"rapid": RECORD},
                 "by_color": {"white": RECORD}},
    "rating-history": [{"time_class": "rapid", "day": "2026-11-01", "rating": 750, "games": 2}],
    "openings": [{"opening": "Scotch Game", "color": "white", "eco": "C45", **RECORD}],
    "accuracy": {
        "coverage": {"analysed_games": 1, "total_games": 2, "percent": "50.0"},
        "summary": {"accuracy": "78.4", "inaccuracies_per_game": "2.00",
                    "mistakes_per_game": "0.00", "blunders_per_game": "1.00"},
        "games": [{"game_id": 9, "end_time": "2026-11-01T10:00:00Z", "time_class": "rapid",
                   "color": "white", "outcome": "win", "accuracy": "78.4",
                   "source_accuracy": "70.1", "moves": 30, "inaccuracies": 2,
                   "mistakes": 0, "blunders": 1}],
    },
}  # fmt: skip


class FakeApi:
    def __init__(self, known: set[str]) -> None:
        self.known, self.calls = known, []

    def stats(self, username: str, view: str, params: dict) -> object:
        self.calls.append((username, view, params))
        if username not in self.known:
            raise NotFound(f"player {username!r} not found")
        return STATS[view]


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    api = FakeApi(known={"me"})
    monkeypatch.setattr(session, "client", lambda: api)
    st.cache_data.clear()
    return api


def run(username: str = "", **state) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=30)
    app.session_state["username"] = username
    for key, value in state.items():
        app.session_state[key] = value
    return app.run()


def test_asks_for_a_username_first(fake_api: FakeApi) -> None:
    app = run()
    assert not app.exception
    assert "Enter a Chess.com username" in app.info[0].value
    assert fake_api.calls == []


def test_shows_the_statistics_of_a_player(fake_api: FakeApi) -> None:
    app = run("me")
    assert not app.exception
    assert app.header[0].value == "me"
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Games"] == "2" and metrics["Accuracy"] == "78.4 %"
    assert "cover 1 of 2 games (50.0 %)" in app.caption[0].value
    assert len(app.get("vega_lite_chart")) == 4  # results x2, rating history, accuracy
    assert len(app.dataframe) == 1  # openings as White (the fake has none as Black)
    assert [s.value for s in app.subheader][:3] == [
        "Results by time class", "Results by colour", "Rating history",
    ]  # fmt: skip


def test_filters_are_sent_to_the_api(fake_api: FakeApi) -> None:
    run("me", time_class="rapid")
    assert {params.get("time_class") for _, _, params in fake_api.calls} == {"rapid"}


def test_an_unknown_player_gets_a_hint_not_a_crash(fake_api: FakeApi) -> None:
    app = run("ghost")
    assert not app.exception
    assert "hasn't been synced yet" in app.warning[0].value


def test_rating_chart_labels_month_and_year_and_zooms_only_in_time() -> None:
    from dashboard.stats_page import rating_chart
    from dashboard.tables import rating_frame

    spec = rating_chart(rating_frame(STATS["rating-history"])).to_dict()

    assert spec["encoding"]["x"]["axis"]["format"] == "%b '%y"  # e.g. Jul '25
    (zoom,) = spec["params"]
    assert zoom["bind"] == "scales" and zoom["select"]["encodings"] == ["x"]
