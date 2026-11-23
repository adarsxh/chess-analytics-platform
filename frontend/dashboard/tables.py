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
