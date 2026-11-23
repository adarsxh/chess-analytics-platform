"""State shared by every page: the API client, the selected player and the filters.

Streamlit re-runs the whole script on every interaction; `st.session_state` keeps values
between runs, `st.cache_resource` keeps one API client, `st.cache_data` caches responses.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import streamlit as st

from dashboard.api import ApiClient, filter_params

CACHE_SECONDS = 60  # stats change only after a sync or an analysis


@dataclass(frozen=True)
class Selection:
    username: str
    time_class: str | None
    since: date | None
    until: date | None

    @property
    def params(self) -> dict[str, str]:
        return filter_params(self.time_class, self.since, self.until)


@st.cache_resource
def client() -> ApiClient:
    return ApiClient.from_env()


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def stats(username: str, view: str, params: tuple[tuple[str, str], ...]) -> Any:
    """Cached per player, view and filters (`params` is a tuple so it can be hashed)."""
    return client().stats(username, view, dict(params))


def selection() -> Selection | None:
    """The player and filters chosen in the sidebar; None until a player is entered."""
    username = st.session_state.get("username", "").strip()
    if not username:
        return None
    since, until = (list(st.session_state.get("dates") or ()) + [None, None])[:2]
    time_class = st.session_state.get("time_class")
    return Selection(username, None if time_class == "all" else time_class, since, until)
