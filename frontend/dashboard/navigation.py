"""The app's pages, defined once so pages can link to each other (st.switch_page)."""

import streamlit as st

from dashboard import accuracy_page, game_page, games_page, openings_page, overview_page

# The default page is always served at "/", so it takes no url_path.
OVERVIEW = st.Page(overview_page.render, title="Overview", icon="📊", default=True)
OPENINGS = st.Page(openings_page.render, title="Openings", icon="📖", url_path="openings")
ACCURACY = st.Page(accuracy_page.render, title="Accuracy", icon="🎯", url_path="accuracy")
GAMES = st.Page(games_page.render, title="Games", icon="📋", url_path="games")
GAME = st.Page(game_page.render, title="Game", icon="🔍", url_path="game")

# Sidebar sections: statistics over all (filtered) games, then single games.
SECTIONS = {"Statistics": [OVERVIEW, OPENINGS, ACCURACY], "Games": [GAMES, GAME]}
