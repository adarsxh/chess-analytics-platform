"""Overview page (the default): headline numbers, results, rating history."""

import altair as alt
import streamlit as st

from dashboard.stats import OUTCOME_COLORS, load, time_axis
from dashboard.tables import COLORS, OUTCOMES, TIME_CLASSES, rating_frame, results_frame


def render() -> None:
    loaded = load("Overview", "rating-history", "accuracy")
    if loaded is None:
        return
    _headline(loaded["overview"], loaded["accuracy"])
    _results(loaded["overview"])
    _rating_history(loaded["rating-history"])


def _headline(overview: dict, accuracy: dict) -> None:
    total, summary, coverage = overview["total"], accuracy["summary"], accuracy["coverage"]
    columns = st.columns(4)
    columns[0].metric("Games", f"{total['games']:,}")
    columns[1].metric("Score", f"{total['score_percent']} %", help="Win = 1 point, draw = ½")
    columns[2].metric(
        "Accuracy",
        f"{summary['accuracy']} %" if summary["accuracy"] is not None else "–",
        help="This project's accuracy (ADR-026), not Chess.com's",
    )
    columns[3].metric("Blunders / game", summary["blunders_per_game"] or "–")
    st.caption(
        f"{total['wins']:,} wins · {total['draws']:,} draws · {total['losses']:,} losses. "
        f"Accuracy and errors cover {coverage['analysed_games']:,} of {coverage['total_games']:,}"
        f" games ({coverage['percent']} %): only analysed games have engine statistics."
    )


def _results(overview: dict) -> None:
    left, right = st.columns(2)
    for column, title, breakdown, order in (
        (left, "Results by time class", overview["by_time_class"], TIME_CLASSES),
        (right, "Results by colour", overview["by_color"], COLORS),
    ):
        column.subheader(title)
        frame = results_frame(breakdown, order)
        chart = (
            alt.Chart(frame)
            .mark_bar()
            .encode(
                x=alt.X(
                    "games:Q", stack="normalize", title="Share of games", axis=alt.Axis(format="%")
                ),  # fmt: skip
                y=alt.Y("group:N", sort=order, title=None),
                color=alt.Color("outcome:N", scale=OUTCOME_COLORS, sort=OUTCOMES, title=None),
                order=alt.Order("outcome_order:Q"),
                tooltip=["group", "outcome", "games"],
            )
            .transform_calculate(
                outcome_order="indexof(['wins', 'draws', 'losses'], datum.outcome)"
            )
        )
        column.altair_chart(chart, width="stretch")


def _rating_history(points: list[dict]) -> None:
    st.subheader("Rating history")
    if not points:
        st.caption("No rated games in this range.")
        return
    frame = rating_frame(points)
    # One time class at a time: each has its own rating, and lines of rarely played classes
    # would otherwise cut straight across years without games. Default: the most played.
    played = frame.groupby("time_class")["games"].sum().sort_values(ascending=False)
    choice = st.radio("Time class", list(played.index), horizontal=True,
                      label_visibility="collapsed", key="rating_time_class")  # fmt: skip
    st.altair_chart(rating_chart(frame[frame["time_class"] == choice]), width="stretch")
    st.caption("One point per day played: the rating after that day's last rated game. "
               "Drag to pan, scroll to zoom, double-click to reset.")  # fmt: skip


def rating_chart(frame) -> alt.Chart:
    """Rating over time. Zoom and pan move only the time axis; the rating axis stays fixed."""
    return (
        alt.Chart(frame)
        .mark_line(point=alt.OverlayMarkDef(size=12))
        .encode(
            x=alt.X("day:T", title=None, axis=time_axis()),
            y=alt.Y("rating:Q", scale=alt.Scale(zero=False), title="Rating"),
            tooltip=[alt.Tooltip("day:T", format="%d %b %Y"), "time_class", "rating", "games"],
        )
        .interactive(bind_y=False)
    )
