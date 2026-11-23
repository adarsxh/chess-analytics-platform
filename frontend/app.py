"""Dashboard entry point: `streamlit run app.py` (from the frontend/ directory).

The sidebar holds what every page shares (player, time class, dates); each page reads it
through dashboard.session and fetches what it needs from the API.
"""

import streamlit as st

from dashboard import jobs, navigation, session
from dashboard.tables import TIME_CLASSES

SYNC_JOB = "sync_job"

st.set_page_config(page_title="Chess Analytics", page_icon="♟", layout="wide")

with st.sidebar:
    st.title("♟ Chess Analytics")
    st.text_input("Chess.com username", key="username", placeholder="e.g. hikaru")
    st.selectbox("Time class", ["all", *TIME_CLASSES], key="time_class")
    st.date_input("Dates (optional)", value=(), key="dates", format="YYYY-MM-DD")

    selected = session.selection()
    jobs.show_result(SYNC_JOB, "Sync")
    if jobs.active(SYNC_JOB):
        jobs.follow(SYNC_JOB, "Sync")
    elif st.button(
        "Sync from Chess.com",
        disabled=selected is None,
        width="stretch",
        help="Fetch new games; the 20 newest are then analysed in the background",
    ):
        jobs.start(SYNC_JOB, lambda: session.client().request_sync(selected.username))
        st.rerun()

st.navigation([navigation.STATS, navigation.GAMES, navigation.GAME]).run()
