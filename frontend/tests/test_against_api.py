"""The dashboard against the real API of the e2e stack: the contract check.

test_app.py uses a hand-written fake API; this runs the same pages against the backend's
real responses (the stub player, synced and analysed by the real worker and Stockfish).
    uv run pytest -m e2e      (with the e2e stack running; see tests/e2e/compose.yaml)
"""

import os
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.e2e

API = os.environ.get("CHESS_E2E_API_URL", "http://127.0.0.1:18000")
APP = str(Path(__file__).resolve().parents[1] / "app.py")
PLAYER = "e2e_player"  # synced by the backend's e2e tests, which run first in CI


@pytest.fixture(autouse=True)
def real_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHESS_DASHBOARD_API_URL", API)
    st.cache_resource.clear()
    st.cache_data.clear()


def test_statistics_page_renders_real_responses() -> None:
    app = AppTest.from_file(APP, default_timeout=30)
    app.session_state["username"] = PLAYER
    app.run()

    assert not app.exception and not app.error and not app.warning
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Games"] == "2"
    assert "cover 2 of 2 games" in app.caption[0].value
    assert len(app.get("vega_lite_chart")) == 4
