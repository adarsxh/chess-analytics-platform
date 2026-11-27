""" "Analyse all" (ADR-028): analysis coverage, and buttons to start or stop the backlog.

Only the 20 newest games are analysed after a sync; the rest of the history costs real
compute (≈40 s per game per worker), so it runs only when asked for, behind every other
job, and can be stopped at any time. While it runs, the panel refreshes itself every few
seconds as a fragment; when it ends, the whole page reloads with the new statistics.
"""

import streamlit as st

from dashboard import session
from dashboard.api import ApiError

POLL_SECONDS = 5
SECONDS_PER_GAME = 40  # one worker, Stockfish at 300k nodes per position (ADR-023)


def render(username: str) -> None:
    st.subheader("Analyse older games")
    progress = _progress(username)
    if progress is None:
        return
    if _active(progress):
        _follow(username)
    else:
        _idle(username, progress)


def _progress(username: str) -> dict | None:
    try:
        return session.client().analysis_progress(username)
    except ApiError as exc:
        st.warning(f"Can't read the analysis progress: {exc}")
        return None


def _active(progress: dict) -> bool:
    return progress["expanding"] or progress["queued"] + progress["running"] > 0


def _bar(progress: dict) -> None:
    analysed, total = progress["analysed_games"], progress["total_games"]
    share = analysed / total if total else 0.0
    st.progress(share, text=f"{analysed:,} of {total:,} games analysed ({share:.0%})")


def _idle(username: str, progress: dict) -> None:
    _bar(progress)
    remaining = progress["total_games"] - progress["analysed_games"]
    if remaining <= 0:
        st.caption("Every game is analysed.")
        return
    hours = remaining * SECONDS_PER_GAME / 3600
    if st.button(
        f"Analyse all {remaining:,} remaining games",
        help=f"About {hours:,.0f} hours on one worker, less with more workers (CHESS_WORKERS)."
        " Runs behind everything else, and can be stopped at any time.",
    ):
        try:
            session.client().request_analyse_all(username)
        except ApiError as exc:
            st.error(f"Couldn't start: {exc}")
            return
        st.rerun()
    st.caption(
        f"Only analysed games count in accuracy and errors. ≈{SECONDS_PER_GAME} s per game per "
        "worker; new games after a sync and games you open are still analysed first."
    )


@st.fragment(run_every=POLL_SECONDS)
def _follow(username: str) -> None:
    progress = _progress(username)
    if progress is None:
        return
    if not _active(progress):  # finished (or stopped and drained): show the new numbers
        st.cache_data.clear()
        st.rerun()
    _bar(progress)
    if progress["expanding"] and not progress["queued"]:
        waiting = "Preparing the list of games"
    else:
        waiting = f"{progress['queued']:,} waiting · {progress['running']} being analysed now"
    st.caption(f"{waiting}. This refreshes every {POLL_SECONDS} s; statistics update when it ends.")
    if not (progress["queued"] or progress["expanding"]):
        return  # only running analyses left: nothing to withdraw, they finish shortly
    if st.button(
        "Stop",
        help='Withdraw the games "Analyse all" queued that are still waiting; games '
        "being analysed finish, and new games from a sync or opened by you are kept.",
    ):
        try:
            stopped = session.client().stop_analyse_all(username)
        except ApiError as exc:
            st.error(f"Couldn't stop: {exc}")
            return
        st.session_state["analyse_all:stopped"] = stopped["cancelled"]
        st.cache_data.clear()
        st.rerun()


def show_stopped() -> None:
    """After Stop: one message saying how many games were withdrawn."""
    cancelled = st.session_state.pop("analyse_all:stopped", None)
    if cancelled is not None:
        st.success(f"Stopped: {cancelled:,} games withdrawn. Analysed games are kept.")
