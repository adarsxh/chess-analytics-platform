"""Openings page: results per opening family, as White and as Black."""

import streamlit as st

from dashboard.stats import load
from dashboard.tables import COLORS, openings_frame


def render() -> None:
    loaded = load("Openings", "openings")
    if loaded is None:
        return
    frame = openings_frame(loaded["openings"])
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
                "games": "Games",
                "wins": "Wins",
                "draws": "Draws",
                "losses": "Losses",
                "score_percent": st.column_config.ProgressColumn(
                    "Score", format="%.1f %%", min_value=0, max_value=100
                ),
            },
        )
    st.caption("Grouped by opening family from Chess.com's names (e.g. all Scotch Game lines).")
