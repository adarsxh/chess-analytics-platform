"""Statistics page: results, rating history, openings, accuracy (ADR-026 views)."""

import altair as alt
import streamlit as st

from dashboard import session
from dashboard.api import ApiError, NotFound
from dashboard.tables import (
    COLORS,
    OUTCOMES,
    TIME_CLASSES,
    accuracy_frame,
    openings_frame,
    rating_frame,
    results_frame,
)

OUTCOME_COLORS = alt.Scale(domain=OUTCOMES, range=["#4c9a5b", "#9e9e9e", "#c4524a"])


def render() -> None:
    selected = session.selection()
    if selected is None:
        st.info("Enter a Chess.com username in the sidebar to see statistics.")
        return
    params = tuple(sorted(selected.params.items()))
    try:
        overview = session.stats(selected.username, "overview", params)
        history = session.stats(selected.username, "rating-history", params)
        openings = session.stats(selected.username, "openings", params)
        accuracy = session.stats(selected.username, "accuracy", params)
    except NotFound:
        st.warning(f"**{selected.username}** hasn't been synced yet.")
        return
    except ApiError as exc:
        st.error(f"The API returned an error: {exc}")
        return

    st.header(f"{selected.username}")
    if overview["total"]["games"] == 0:
        st.info("No games match these filters.")
        return
    _headline(overview, accuracy)
    _results(overview)
    _rating_history(history)
    _openings(openings)
    _accuracy(accuracy)


def _headline(overview: dict, accuracy: dict) -> None:
    total, summary, coverage = overview["total"], accuracy["summary"], accuracy["coverage"]
    columns = st.columns(5)
    columns[0].metric("Games", f"{total['games']:,}")
    columns[1].metric("Score", f"{total['score_percent']} %", help="Win = 1 point, draw = ½")
    columns[2].metric("W / D / L", f"{total['wins']} / {total['draws']} / {total['losses']}")
    columns[3].metric(
        "Accuracy",
        f"{summary['accuracy']} %" if summary["accuracy"] is not None else "–",
        help="This project's accuracy (ADR-026), not Chess.com's",
    )
    columns[4].metric("Blunders / game", summary["blunders_per_game"] or "–")
    st.caption(
        f"Accuracy and errors cover {coverage['analysed_games']} of {coverage['total_games']}"
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
    chart = (
        alt.Chart(rating_frame(points))
        .mark_line(point=alt.OverlayMarkDef(size=12))
        .encode(
            x=alt.X("day:T", title=None),
            y=alt.Y("rating:Q", scale=alt.Scale(zero=False), title="Rating"),
            color=alt.Color("time_class:N", sort=TIME_CLASSES, title="Time class"),
            tooltip=["day:T", "time_class", "rating", "games"],
        )
    )
    st.altair_chart(chart, width="stretch")


def _openings(openings: list[dict]) -> None:
    st.subheader("Openings")
    frame = openings_frame(openings)
    for tab, color in zip(st.tabs(["As White", "As Black"]), COLORS, strict=True):
        rows = frame[frame["color"] == color].drop(columns="color")
        if rows.empty:
            tab.caption("No games.")
            continue
        tab.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            column_config={
                "opening": "Opening",
                "eco": "ECO",
                "score_percent": st.column_config.ProgressColumn(
                    "Score", format="%.1f %%", min_value=0, max_value=100
                ),
            },
        )
    st.caption("Grouped by opening family from Chess.com's names (e.g. all Scotch Game lines).")


def _accuracy(accuracy: dict) -> None:
    st.subheader("Accuracy per analysed game")
    frame = accuracy_frame(accuracy["games"])
    if frame.empty:
        st.caption("No analysed games yet. The latest 20 are analysed after each sync.")
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
        .mark_line(point=True)
        .encode(
            x=alt.X("end_time:T", title=None),
            y=alt.Y("value:Q", scale=alt.Scale(domain=[0, 100]), title="Accuracy %"),
            color=alt.Color("source:N", title=None),
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
