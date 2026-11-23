"""The app's pages, defined once so pages can link to each other (st.switch_page)."""

import streamlit as st

from dashboard import game_page, games_page, stats_page

# The default page is always served at "/", so it takes no url_path.
STATS = st.Page(stats_page.render, title="Statistics", icon="📊", default=True)
GAMES = st.Page(games_page.render, title="Games", icon="📋", url_path="games")
GAME = st.Page(game_page.render, title="Game", icon="🔍", url_path="game")
