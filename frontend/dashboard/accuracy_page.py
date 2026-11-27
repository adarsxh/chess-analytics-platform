"""Accuracy page: engine statistics of the analysed games (ADR-026)."""

import altair as alt
import streamlit as st

from dashboard import analyse_all
from dashboard.stats import MONTH_YEAR, load, time_axis
from dashboard.tables import ERROR_KINDS, accuracy_frame, monthly_frame


def render() -> None:
    loaded = load("Accuracy", "accuracy", "accuracy-by-month")
    if loaded is None:
        return
    accuracy = loaded["accuracy"]
    _summary(accuracy)
    analyse_all.show_stopped()
    analyse_all.render(loaded.username)
    _by_month(loaded["accuracy-by-month"])
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


def _by_month(months: list[dict]) -> None:
    st.subheader("By month")
    frame = monthly_frame(months)
    if frame.empty:
        st.caption("No analysed games yet.")
        return
    left, right = st.columns(2)
    left.altair_chart(accuracy_by_month_chart(frame), width="stretch")
    right.altair_chart(errors_by_month_chart(frame), width="stretch")
    st.caption(
        "One point per calendar month (UTC), over the games analysed in it; a bigger dot is "
        "more games. Months with only a few analysed games are noisy."
    )


def _month_tooltip() -> list:
    return [
        alt.Tooltip("month:T", title="Month", format="%B %Y"),
        alt.Tooltip("analysed_games:Q", title="Analysed games"),
        alt.Tooltip("total_games:Q", title="Games played"),
    ]


def month_axis() -> alt.Axis:
    """Ticks on month starts only: the data has one point per month, so day ticks
    ("09 Oct") between two months would suggest detail that isn't there."""
    return alt.Axis(format=MONTH_YEAR, tickCount={"interval": "month", "step": 1})


def accuracy_by_month_chart(frame) -> alt.Chart:
    base = alt.Chart(frame).encode(
        x=alt.X("month:T", title=None, axis=month_axis()),
        # The full 0–100 scale, like the per-game chart: an auto-zoomed axis turns a change
        # of under one point into a steep line.
        y=alt.Y("accuracy:Q", scale=alt.Scale(domain=[0, 100]), title="Accuracy %"),
    )
    dots = base.mark_circle(opacity=0.9).encode(
        size=alt.Size("analysed_games:Q", title="Analysed games", legend=None),
        tooltip=[*_month_tooltip(), alt.Tooltip("accuracy:Q", title="Accuracy %")],
    )
    return (base.mark_line(opacity=0.5) + dots).properties(title="Accuracy")


def errors_by_month_chart(frame) -> alt.Chart:
    long = frame.melt(
        id_vars=["month", "analysed_games", "total_games"],
        value_vars=[f"{kind}_per_game" for kind in ERROR_KINDS],
        var_name="kind",
        value_name="per_game",
    )
    long["kind"] = long["kind"].str.removesuffix("_per_game")
    color = alt.Color(
        "kind:N", title=None, sort=ERROR_KINDS,
        scale=alt.Scale(domain=ERROR_KINDS, range=["#e3b341", "#e07b39", "#c4524a"]),
    )  # fmt: skip
    return (
        alt.Chart(long)
        .mark_line(point=True)
        .encode(
            x=alt.X("month:T", title=None, axis=month_axis()),
            y=alt.Y("per_game:Q", title="Per game"),
            color=color,
            tooltip=[*_month_tooltip(), "kind", alt.Tooltip("per_game:Q", title="Per game")],
        )
        .properties(title="Errors per game")
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
