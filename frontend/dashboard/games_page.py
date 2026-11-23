"""Games page: the player's games, newest first. Select a row to open the game."""

import pandas as pd
import streamlit as st

from dashboard import session
from dashboard.api import ApiError, NotFound

PAGE_SIZE = 50


def render() -> None:
    selected = session.selection()
    if selected is None:
        st.info("Enter a Chess.com username in the sidebar to list games.")
        return
    page = st.session_state.get("games_page", 1)
    try:
        listing = session.client().games(selected.username, limit=PAGE_SIZE,
                                         offset=(page - 1) * PAGE_SIZE,
                                         time_class=selected.time_class)  # fmt: skip
        quality = session.stats(
            selected.username, "accuracy", tuple(sorted(selected.params.items()))
        )
    except NotFound:
        st.warning(f"**{selected.username}** hasn't been synced yet.")
        return
    except ApiError as exc:
        st.error(f"The API returned an error: {exc}")
        return

    st.header(f"Games of {selected.username}")
    pages = max(1, -(-listing["total"] // PAGE_SIZE))
    st.caption(f"{listing['total']:,} games · page {page} of {pages}. Select a row to open it.")
    if not listing["items"]:
        st.info("No games match these filters.")
        return

    accuracy = {g["game_id"]: g["accuracy"] for g in quality["games"]}
    frame = games_frame(listing["items"], accuracy)
    event = st.dataframe(
        frame.drop(columns="game_id"),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "end_time": st.column_config.DatetimeColumn("Date", format="YYYY-MM-DD HH:mm"),
            "time_control": "Time control",
            "color": "Colour",
            "outcome": "Result",
            "opponent": "Opponent",
            "rating": "Rating",
            "accuracy": st.column_config.NumberColumn(
                "Accuracy", format="%.1f", help="Ours, for analysed games"
            ),
            "opening": "Opening",
        },
    )
    if event.selection.rows:
        st.session_state["game_id"] = int(frame.iloc[event.selection.rows[0]]["game_id"])
        from dashboard import navigation  # here, not at the top: navigation imports this page

        st.switch_page(navigation.GAME)
    st.number_input("Page", min_value=1, max_value=pages, key="games_page")


def games_frame(items: list[dict], accuracy: dict[int, str | None]) -> pd.DataFrame:
    rows = [
        {
            "game_id": g["game_id"],
            "end_time": pd.Timestamp(g["end_time"]),
            "time_control": f"{g['time_class']} {g['time_control']}",
            "color": g["color"],
            "outcome": g["outcome"],
            "opponent": f"{g['opponent_username']} ({g['opponent_rating_after'] or '?'})",
            "rating": g["rating_after"],
            "accuracy": None
            if accuracy.get(g["game_id"]) is None
            else float(accuracy[g["game_id"]]),
            "opening": g["opening_name"],  # last: the widest column
        }
        for g in items
    ]
    return pd.DataFrame(rows)
