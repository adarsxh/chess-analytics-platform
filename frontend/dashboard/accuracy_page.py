"""Accuracy page: engine statistics of the analysed games (ADR-026)."""

import altair as alt
import streamlit as st

from dashboard import analyse_all
from dashboard.stats import load, time_axis
from dashboard.tables import accuracy_frame


def render() -> None:
    loaded = load("Accuracy", "accuracy")
    if loaded is None:
        return
    accuracy = loaded["accuracy"]
    _summary(accuracy)
    analyse_all.show_stopped()
    analyse_all.render(loaded.username)
    _per_game(accuracy)


def _summary(accuracy: dict) -> None:
    summary, coverage = accuracy["summary"], accuracy["coverage"]
    columns = st.columns(4)
    columns[0].metric(
        "Accuracy",
        f"{summary['accuracy']} %" if summary["accuracy"] is not None else "–",
        help="This project's accuracy (ADR-026), not Chess.com's",
    )
    for column, label, key in zip(
        columns[1:],
        ("Inaccuracies / game", "Mistakes / game", "Blunders / game"),
        ("inaccuracies_per_game", "mistakes_per_game", "blunders_per_game"),
        strict=True,
    ):
        column.metric(label, summary[key] or "–")
    st.caption(
        f"Covers {coverage['analysed_games']:,} of {coverage['total_games']:,} games "
        f"({coverage['percent']} %) matching the filters: only analysed games have engine "
        "statistics. The latest 20 are analysed after each sync; older ones below, or one at "
        "a time from the game page."
    )


def _per_game(accuracy: dict) -> None:
    st.subheader("Accuracy per analysed game")
    frame = accuracy_frame(accuracy["games"])
    if frame.empty:
        st.caption("No analysed games yet.")
        return
    long = frame.melt(
        id_vars=["end_time", "game_id", "outcome", "blunders"],
        value_vars=["accuracy", "source_accuracy"],
        var_name="source",
        value_name="value",
    ).dropna(subset=["value"])
    long["source"] = long["source"].map({"accuracy": "Ours", "source_accuracy": "Chess.com"})
    chart = (
        alt.Chart(long)
        .mark_circle(size=70, opacity=0.85)  # dots: games are separate events, not a series
        .encode(
            x=alt.X("end_time:T", title=None, axis=time_axis()),
            y=alt.Y("value:Q", scale=alt.Scale(domain=[0, 100]), title="Accuracy %"),
            color=alt.Color(
                "source:N",
                title=None,
                scale=alt.Scale(domain=["Ours", "Chess.com"], range=["#4c8bf5", "#f2a541"]),
            ),
            tooltip=["end_time:T", "game_id", "outcome", "source", "value", "blunders"],
        )
    )
    st.altair_chart(chart, width="stretch")
    errors = frame[["inaccuracies", "mistakes", "blunders"]].sum()
    st.caption(
        f"{len(frame)} games: {errors['inaccuracies']} inaccuracies, {errors['mistakes']} "
        f"mistakes, {errors['blunders']} blunders. Chess.com reports accuracy for only some "
        "games, and uses its own (private) formula."
    )
