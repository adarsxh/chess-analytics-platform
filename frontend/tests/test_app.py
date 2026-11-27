"""The statistics pages (Overview, Openings, Accuracy), run by AppTest against a fake API.

The Overview is the default page, so it runs through the real app.py (sidebar included);
the other pages run on their own via AppTest.from_function.
"""

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


EMPTY = {"total": {**RECORD, "games": 0}, "by_time_class": {}, "by_color": {}}


class FakeApi:
    def __init__(self, known: set[str]) -> None:
        self.known, self.calls = known, []
        self.overview = STATS["overview"]

    def stats(self, username: str, view: str, params: dict) -> object:
        self.calls.append((username, view, params))
        if username not in self.known:
            raise NotFound(f"player {username!r} not found")
        return self.overview if view == "overview" else STATS[view]


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    api = FakeApi(known={"me"})
    monkeypatch.setattr(session, "client", lambda: api)
    st.cache_data.clear()
    return api


def openings_script() -> None:
    from dashboard import openings_page

    openings_page.render()


def accuracy_script() -> None:
    from dashboard import accuracy_page

    accuracy_page.render()


def run(username: str = "", page=None, **state) -> AppTest:
    """The whole app (Overview) by default, or one page function on its own."""
    if page is None:
        app = AppTest.from_file(APP, default_timeout=30)
    else:
        app = AppTest.from_function(page, default_timeout=30)
    app.session_state["username"] = username
    for key, value in state.items():
        app.session_state[key] = value
    return app.run()


def views(api: FakeApi) -> set[str]:
    return {view for _, view, _ in api.calls}


ALL_PAGES = pytest.mark.parametrize("page", [None, openings_script, accuracy_script],
                                    ids=["overview", "openings", "accuracy"])  # fmt: skip


@ALL_PAGES
def test_asks_for_a_username_first(fake_api: FakeApi, page) -> None:
    app = run(page=page)
    assert not app.exception
    assert "Enter a Chess.com username" in app.info[0].value
    assert fake_api.calls == []


@ALL_PAGES
def test_an_unknown_player_gets_a_hint_not_a_crash(fake_api: FakeApi, page) -> None:
    app = run("ghost", page=page)
    assert not app.exception
    assert "hasn't been synced yet" in app.warning[0].value


@ALL_PAGES
def test_filters_are_sent_to_the_api(fake_api: FakeApi, page) -> None:
    run("me", page=page, time_class="rapid")
    assert fake_api.calls
    assert {params.get("time_class") for _, _, params in fake_api.calls} == {"rapid"}


@ALL_PAGES
def test_no_matching_games_says_so(fake_api: FakeApi, page) -> None:
    fake_api.overview = EMPTY
    app = run("me", page=page)
    assert not app.exception
    assert app.info[0].value == "No games match these filters."
    assert len(app.metric) == 0


def test_overview_shows_headline_results_and_rating(fake_api: FakeApi) -> None:
    app = run("me")
    assert not app.exception
    assert app.header[0].value == "Overview · me"
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Games"] == "2" and metrics["Accuracy"] == "78.4 %"
    assert "cover 1 of 2 games (50.0 %)" in app.caption[0].value
    assert len(app.get("vega_lite_chart")) == 3  # results x2, rating history
    assert [s.value for s in app.subheader] == [
        "Results by time class", "Results by colour", "Rating history",
    ]  # fmt: skip
    assert views(fake_api) == {"overview", "rating-history", "accuracy"}


def test_openings_page_shows_only_openings(fake_api: FakeApi) -> None:
    app = run("me", page=openings_script)
    assert not app.exception
    assert app.header[0].value == "Openings · me"
    assert [t.label for t in app.tabs] == ["As White", "As Black"]
    (table,) = app.dataframe  # the fake has openings as White only
    assert table.value["opening"].tolist() == ["Scotch Game"]
    assert views(fake_api) == {"overview", "openings"}


def test_accuracy_page_shows_error_rates_and_games(fake_api: FakeApi) -> None:
    app = run("me", page=accuracy_script)
    assert not app.exception
    assert app.header[0].value == "Accuracy · me"
    assert {m.label: m.value for m in app.metric} == {
        "Accuracy": "78.4 %", "Inaccuracies / game": "2.00",
        "Mistakes / game": "0.00", "Blunders / game": "1.00",
    }  # fmt: skip
    assert "Covers 1 of 2 games (50.0 %)" in app.caption[0].value
    assert len(app.get("vega_lite_chart")) == 1
    assert views(fake_api) == {"overview", "accuracy"}


def test_sidebar_groups_statistics_and_games() -> None:
    from dashboard import navigation

    assert {section: [p.title for p in pages]
            for section, pages in navigation.SECTIONS.items()} == {
        "Statistics": ["Overview", "Openings", "Accuracy"],
        "Games": ["Games", "Game"],
    }  # fmt: skip


def test_rating_chart_labels_months_with_year_and_zooms_only_in_time() -> None:
    from dashboard.overview_page import rating_chart
    from dashboard.tables import rating_frame

    spec = rating_chart(rating_frame(STATS["rating-history"])).to_dict()

    label = spec["encoding"]["x"]["axis"]["labelExpr"]
    assert "date(datum.value) == 1" in label  # month starts: "Jul '25"; other ticks: "10 Nov"
    assert "%b '%y" in label and "%d %b" in label
    (zoom,) = spec["params"]
    assert zoom["bind"] == "scales" and zoom["select"]["encodings"] == ["x"]
