"""API JSON → pandas DataFrames shaped for charts and tables. Pure functions, no Streamlit."""

from typing import Any

import pandas as pd

TIME_CLASSES = ["bullet", "blitz", "rapid", "daily"]
COLORS = ["white", "black"]
OUTCOMES = ["wins", "draws", "losses"]


def results_frame(breakdown: dict[str, dict[str, Any]], order: list[str]) -> pd.DataFrame:
    """One row per (group, outcome) with a count: a stacked W/D/L bar per group."""
    rows = [
        {"group": group, "outcome": outcome, "games": breakdown[group][outcome]}
        for group in order
        if group in breakdown
        for outcome in OUTCOMES
    ]
    return pd.DataFrame(rows, columns=["group", "outcome", "games"])


def rating_frame(points: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(points, columns=["time_class", "day", "rating", "games"])
    frame["day"] = pd.to_datetime(frame["day"])
    return frame


def openings_frame(openings: list[dict[str, Any]]) -> pd.DataFrame:
    columns = ["opening", "color", "eco", "games", "wins", "draws", "losses", "score_percent"]
    frame = pd.DataFrame(openings, columns=columns)
    frame["score_percent"] = pd.to_numeric(frame["score_percent"])
    return frame


def accuracy_frame(games: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per analysed game, oldest first, with ours and Chess.com's accuracy."""
    columns = ["game_id", "end_time", "time_class", "color", "outcome", "accuracy",
               "source_accuracy", "moves", "inaccuracies", "mistakes", "blunders"]  # fmt: skip
    frame = pd.DataFrame(games, columns=columns)
    frame["end_time"] = pd.to_datetime(frame["end_time"])
    for column in ("accuracy", "source_accuracy"):
        frame[column] = pd.to_numeric(frame[column])
    return frame.sort_values("end_time", ignore_index=True)


EVAL_CLAMP = 10  # pawns; beyond this the game is decided and the chart would flatten
LABEL_SYMBOLS = {"blunder": "??", "mistake": "?", "inaccuracy": "?!", "best": "★",
                 "excellent": "!", "good": "", "forced": "□"}  # fmt: skip


def white_moves_first(initial_fen: str) -> bool:
    return initial_fen.split()[1] == "w"


def eval_frame(positions: list[dict[str, Any]], initial_fen: str) -> pd.DataFrame:
    """Evaluation after each ply in pawns, White's point of view, clamped to ±10.
    Mate counts as ±10; `mate_in 0` means the side to move at that ply is checkmated."""
    white_first = white_moves_first(initial_fen)
    rows = []
    for p in positions:
        if p["mate_in"] is None:
            pawns = max(-EVAL_CLAMP, min(EVAL_CLAMP, p["score_cp"] / 100))
        elif p["mate_in"] == 0:
            white_to_move = (p["ply"] % 2 == 0) == white_first
            pawns = -EVAL_CLAMP if white_to_move else EVAL_CLAMP
        else:
            pawns = EVAL_CLAMP if p["mate_in"] > 0 else -EVAL_CLAMP
        rows.append({"ply": p["ply"], "pawns": pawns})
    return pd.DataFrame(rows, columns=["ply", "pawns"])


def moves_frame(moves: list[dict[str, Any]], initial_fen: str) -> pd.DataFrame:
    """One row per move: '12.' or '12...', the move with its symbol, label and loss."""
    white_first = white_moves_first(initial_fen)
    rows = []
    for m in moves:
        # Full-move number: ply 1 is move 1 for White (or for Black, if Black starts).
        number = (m["ply"] + (0 if white_first else 1) + 1) // 2
        rows.append({
            "ply": m["ply"],
            "move": f"{number}." if m["color"] == "white" else f"{number}...",
            "color": m["color"],
            "san": f"{m['san']}{LABEL_SYMBOLS.get(m['label'], '')}",
            "label": m["label"],
            "win_loss": float(m["win_loss"]),
        })  # fmt: skip
    return pd.DataFrame(rows, columns=["ply", "move", "color", "san", "label", "win_loss"])
