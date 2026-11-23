"""Game page: the game, its engine analysis (evaluation graph, labelled moves, accuracy),
and a button to analyse it when it hasn't been (or the analysis is out of date)."""

import altair as alt
import streamlit as st

from dashboard import jobs, session
from dashboard.api import ApiError, NotFound
from dashboard.tables import eval_frame, moves_frame

ERROR_COLORS = alt.Scale(domain=["inaccuracy", "mistake", "blunder"],
                         range=["#e6b422", "#e07b39", "#c4524a"])  # fmt: skip


def render() -> None:
    game_id = _game_id()
    if game_id is None:
        st.info("Open a game from the **Games** page.")
        return
    api = session.client()
    try:
        game = api.game(game_id)
    except NotFound:
        st.warning(f"Game {game_id} doesn't exist.")
        return
    except ApiError as exc:
        st.error(f"The API returned an error: {exc}")
        return
    try:
        analysis = api.analysis(game_id)
    except NotFound:
        analysis = None
    except ApiError as exc:
        st.error(f"Couldn't load the analysis: {exc}")
        return

    _header(game)
    job_key = f"analysis_job:{game_id}"
    jobs.show_result(job_key, "Analysis")
    if jobs.active(job_key):
        jobs.follow(job_key, "Analysis")
        return
    if not _analysis_ready(analysis, job_key, game_id):
        return
    _accuracy(analysis)
    _evaluation(analysis, game["initial_fen"])
    _moves(analysis, game["initial_fen"])


def _game_id() -> int | None:
    """From the URL (?id=…, shareable) or from the Games page selection."""
    raw = st.query_params.get("id") or st.session_state.get("game_id")
    try:
        game_id = int(raw)
    except (TypeError, ValueError):
        return None
    st.query_params["id"] = str(game_id)
    return game_id


def _header(game: dict) -> None:
    white, black = game["white"], game["black"]
    result = {"white_win": "1–0", "black_win": "0–1", "draw": "½–½"}[game["result"]]
    st.header(f"{white['username']} ({white['rating_after'] or '?'}) {result} "
              f"{black['username']} ({black['rating_after'] or '?'})")  # fmt: skip
    st.caption(
        f"{game['end_time'][:10]} · {game['time_class']} {game['time_control']} · "
        f"{game['opening_name'] or 'unknown opening'} · {game['termination'] or ''} · "
        f"[view on Chess.com]({game['url']})"
    )


def _analysis_ready(analysis: dict | None, job_key: str, game_id: int) -> bool:
    """True if there is a usable analysis; otherwise explain why and offer to analyse."""
    if analysis is None:
        message, button = "This game hasn't been analysed yet.", "Analyse with Stockfish"
    elif analysis["status"] in ("pending", "running"):
        message, button = "An analysis of this game is running.", None
    elif analysis["status"] == "failed":
        message, button = f"The last analysis failed: {analysis['error']}", "Try again"
    elif not analysis["fresh"]:
        message, button = "The game changed since it was analysed.", "Analyse again"
    else:
        return True
    st.info(message)
    if button and st.button(button, type="primary"):
        jobs.start(job_key, lambda: session.client().request_analysis(game_id))
        st.rerun()
    elif button is None and st.button("Refresh"):
        st.rerun()
    return False


def _accuracy(analysis: dict) -> None:
    engine = analysis["engine"]
    left, right, info = st.columns(3)
    for column, side in ((left, "white"), (right, "black")):
        value = analysis["accuracy"].get(side)
        column.metric(f"{side.title()} accuracy", "–" if value is None else f"{value} %")
    info.caption(f"{engine['name']} {engine['version']} · {engine['nodes_per_position']:,} "
                 f"nodes per position · classifier v{analysis['classifier_version']}")  # fmt: skip


def _evaluation(analysis: dict, initial_fen: str) -> None:
    st.subheader("Evaluation")
    evals = eval_frame(analysis["positions"], initial_fen)
    moves = moves_frame(analysis["moves"], initial_fen)
    errors = moves[moves["label"].isin(["inaccuracy", "mistake", "blunder"])].merge(evals, on="ply")
    line = (
        alt.Chart(evals)
        .mark_area(line=True, opacity=0.25)
        .encode(
            x=alt.X("ply:Q", title="Ply"),
            y=alt.Y("pawns:Q", scale=alt.Scale(domain=[-10, 10]), title="Pawns (White's view)"),
            tooltip=["ply", alt.Tooltip("pawns:Q", format="+.2f")],
        )
    )
    marks = (
        alt.Chart(errors)
        .mark_point(filled=True, size=90)
        .encode(
            x="ply:Q",
            y="pawns:Q",
            color=alt.Color("label:N", scale=ERROR_COLORS, title=None),
            shape=alt.Shape("color:N", title="Played by"),
            tooltip=["move", "san", "label", alt.Tooltip("win_loss:Q", title="win % lost")],
        )
    )
    st.altair_chart(line + marks, width="stretch")
    st.caption("Above 0 White is better. Capped at ±10 pawns; mate is shown as ±10.")


def _moves(analysis: dict, initial_fen: str) -> None:
    st.subheader("Moves")
    frame = moves_frame(analysis["moves"], initial_fen)
    st.dataframe(
        frame.drop(columns="ply"),
        hide_index=True,
        width="stretch",
        column_config={
            "move": "No.",
            "san": "Move",
            "win_loss": st.column_config.NumberColumn("Win % lost", format="%.2f"),
        },
    )
