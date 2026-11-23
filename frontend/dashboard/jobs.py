"""Start a background job (sync, analysis) and follow it until it finishes.

The job id is kept in `st.session_state[key]`. While it is there, `follow` re-runs every
2 seconds as a fragment (only this small part of the page), and when the job is done it
clears the cached API responses and re-runs the whole page so it shows the new data.
"""

from collections.abc import Callable
from typing import Any

import streamlit as st

from dashboard import session
from dashboard.api import ApiError

POLL_SECONDS = 2
DONE = ("succeeded", "failed")


def start(key: str, request: Callable[[], dict[str, Any]]) -> None:
    """Request the job; remember its id, or show why it couldn't be requested."""
    try:
        st.session_state[key] = request()["job_id"]
    except ApiError as exc:
        st.error(f"Couldn't start: {exc}")


def active(key: str) -> bool:
    return key in st.session_state


@st.fragment(run_every=POLL_SECONDS)
def follow(key: str, label: str) -> None:
    job_id = st.session_state.get(key)
    if job_id is None:
        return
    try:
        job = session.client().job(job_id)
    except ApiError as exc:
        st.warning(f"{label}: can't read the job status ({exc}); retrying…")
        return
    if job["status"] not in DONE:
        attempt = (
            f" (attempt {job['attempts']} of {job['max_attempts']})" if job["attempts"] else ""
        )
        st.info(f"{label}: {job['status']}{attempt}…")
        return
    del st.session_state[key]
    st.session_state[f"{key}:result"] = job
    st.cache_data.clear()  # new games or analyses: every view must refetch
    st.rerun()


def show_result(key: str, label: str) -> None:
    """After `follow` finished: one message saying how the job ended."""
    job = st.session_state.pop(f"{key}:result", None)
    if job is None:
        return
    if job["status"] == "succeeded":
        st.success(f"{label}: done.")
    else:
        st.error(f"{label} failed: {job['last_error']}")
