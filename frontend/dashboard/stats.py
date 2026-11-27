"""What every statistics page shares: loading its views and the common chart settings.

Each page asks only for the stats views it shows (ADR-026), so opening one page doesn't
compute the others. Responses are cached per player, view and filters (session.stats).
"""

from dataclasses import dataclass
from typing import Any

import altair as alt
import streamlit as st

from dashboard import session
from dashboard.api import ApiError, NotFound
from dashboard.tables import OUTCOMES

OUTCOME_COLORS = alt.Scale(domain=OUTCOMES, range=["#4c9a5b", "#9e9e9e", "#c4524a"])
# Month and short year ("Jul '25") on ticks at the start of a month: the default labels name
# the year only at January, so a lone "July" doesn't say which year it is. Over a short range
# the ticks fall inside a month and show the day instead ("10 Nov"), not "Nov '26" repeated.
MONTH_YEAR = "%b '%y"
DAY_MONTH = "%d %b"


def time_axis() -> alt.Axis:
    return alt.Axis(
        labelExpr=f'date(datum.value) == 1 ? timeFormat(datum.value, "{MONTH_YEAR}")'
        f" : timeFormat(datum.value, '{DAY_MONTH}')"
    )


@dataclass(frozen=True)
class Loaded:
    username: str
    views: dict[str, Any]

    def __getitem__(self, view: str) -> Any:
        return self.views[view]


def load(title: str, *views: str) -> Loaded | None:
    """Fetch `views` for the sidebar's player and filters, or explain why there is nothing.

    "overview" is always fetched (it's cheap and cached): its total says whether any game
    matches the filters, so every page handles "no games" the same way.
    """
    selected = session.selection()
    if selected is None:
        st.info("Enter a Chess.com username in the sidebar to see statistics.")
        return None
    params = tuple(sorted(selected.params.items()))
    try:
        fetched = {view: session.stats(selected.username, view, params)
                   for view in dict.fromkeys(("overview", *views))}  # fmt: skip
    except NotFound:
        st.warning(f"**{selected.username}** hasn't been synced yet.")
        return None
    except ApiError as exc:
        st.error(f"The API returned an error: {exc}")
        return None

    st.header(f"{title} · {selected.username}")
    if fetched["overview"]["total"]["games"] == 0:
        st.info("No games match these filters.")
        return None
    return Loaded(selected.username, fetched)
