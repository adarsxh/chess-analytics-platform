"""Dashboard entry point: `streamlit run app.py` (from the frontend/ directory).

The sidebar holds what every page shares (player, time class, dates); each page reads it
through dashboard.session and fetches what it needs from the API.
"""

import streamlit as st

from dashboard import stats_page
from dashboard.tables import TIME_CLASSES

st.set_page_config(page_title="Chess Analytics", page_icon="♟", layout="wide")

with st.sidebar:
    st.title("♟ Chess Analytics")
    st.text_input("Chess.com username", key="username", placeholder="e.g. hikaru")
    st.selectbox("Time class", ["all", *TIME_CLASSES], key="time_class")
    st.date_input("Dates (optional)", value=(), key="dates", format="YYYY-MM-DD")

navigation = st.navigation([st.Page(stats_page.render, title="Statistics", icon="📊",
                                    url_path="stats", default=True)])  # fmt: skip
navigation.run()
