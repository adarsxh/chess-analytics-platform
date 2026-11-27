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


ERROR_KINDS = ["inaccuracies", "mistakes", "blunders"]


def monthly_frame(months: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per month with at least one analysed game, oldest first. Months without
    analysed games have no accuracy, so they are left out rather than drawn as zero."""
    per_game = [f"{kind}_per_game" for kind in ERROR_KINDS]
    columns = ["month", "total_games", "analysed_games", "accuracy", *per_game]
    frame = pd.DataFrame(months, columns=columns)
    frame = frame[frame["analysed_games"] > 0].copy()
    frame["month"] = pd.to_datetime(frame["month"])
    for column in ("accuracy", *per_game):
        frame[column] = pd.to_numeric(frame[column])
    return frame.sort_values("month", ignore_index=True)


PHASES = ["opening", "middlegame", "endgame"]
POSITIONS = ["winning", "equal", "losing"]
BANDS = {
    "much_lower": "25+ lower",
    "lower": "10–24 lower",
    "similar": "within ±9",
    "higher": "10–24 higher",
    "much_higher": "25+ higher",
}


def errors_per_100_frame(rows: list[dict[str, Any]], key: str) -> pd.DataFrame:
    """One row per (bucket, error kind) with its rate per 100 moves, for grouped bars.
    Buckets without moves are kept (rate 0) so every chart shows all three buckets."""
    records = [
        {key: row[key], "kind": kind, "per_100": float(row[f"{kind}_per_100"] or 0),
         "moves": row["moves"],
         "accuracy": float(row["accuracy"]) if row["accuracy"] is not None else None}
        for row in rows
        for kind in ERROR_KINDS
    ]  # fmt: skip
    return pd.DataFrame(records, columns=[key, "kind", "per_100", "moves", "accuracy"])


def opponents_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """One row per opponent band, weakest opponents first, ready for a table."""
    frame = pd.DataFrame(rows, columns=["band", "games", "score_percent", "analysed_games",
                                        "accuracy", "blunders_per_game"])  # fmt: skip
    frame["band"] = frame["band"].map(BANDS)
    for column in ("score_percent", "accuracy", "blunders_per_game"):
        frame[column] = pd.to_numeric(frame[column])
    return frame


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
