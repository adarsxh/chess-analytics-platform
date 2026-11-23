"""API JSON → DataFrames."""

from dashboard.tables import (
    accuracy_frame,
    eval_frame,
    moves_frame,
    openings_frame,
    rating_frame,
    results_frame,
)

RECORD = {"games": 3, "wins": 2, "draws": 0, "losses": 1, "score_percent": "66.7"}


def test_results_frame_follows_the_given_order_and_skips_missing_groups() -> None:
    frame = results_frame({"rapid": RECORD, "bullet": RECORD}, ["bullet", "blitz", "rapid"])
    assert list(frame["group"].unique()) == ["bullet", "rapid"]
    assert frame[frame["group"] == "rapid"].set_index("outcome")["games"].to_dict() == {
        "wins": 2, "draws": 0, "losses": 1,
    }  # fmt: skip


def test_empty_inputs_give_empty_frames_with_columns() -> None:
    assert list(results_frame({}, ["rapid"]).columns) == ["group", "outcome", "games"]
    assert rating_frame([]).empty and accuracy_frame([]).empty and openings_frame([]).empty


def test_accuracy_frame_is_numeric_and_oldest_first() -> None:
    games = [
        {"game_id": 2, "end_time": "2026-11-02T10:00:00Z", "accuracy": "80.5",
         "source_accuracy": None},
        {"game_id": 1, "end_time": "2026-11-01T10:00:00Z", "accuracy": "70.0",
         "source_accuracy": "65.25"},
    ]  # fmt: skip
    frame = accuracy_frame(games)
    assert list(frame["game_id"]) == [1, 2]
    assert frame["accuracy"].tolist() == [70.0, 80.5]
    assert frame["source_accuracy"].isna().tolist() == [False, True]


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
BLACK_TO_MOVE = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def position(ply, cp=None, mate=None) -> dict:
    return {"ply": ply, "score_cp": cp, "mate_in": mate}


def test_eval_frame_is_in_pawns_and_clamped() -> None:
    frame = eval_frame([position(0, cp=35), position(1, cp=-2400), position(2, mate=3),
                        position(3, mate=-1)], START)  # fmt: skip
    assert frame["pawns"].tolist() == [0.35, -10, 10, -10]


def test_mate_0_is_a_loss_for_the_side_to_move() -> None:
    # After ply 4 of a game White started, White is to move: mate 0 = White is mated.
    assert eval_frame([position(4, mate=0)], START)["pawns"].tolist() == [-10]
    assert eval_frame([position(3, mate=0)], START)["pawns"].tolist() == [10]
    # If Black moved first, the sides to move are the other way round.
    assert eval_frame([position(4, mate=0)], BLACK_TO_MOVE)["pawns"].tolist() == [10]


def move(ply, color, san="e4", label="good", loss="3.5") -> dict:
    return {"ply": ply, "color": color, "san": san, "label": label, "win_loss": loss}


def test_moves_frame_numbers_moves_and_adds_symbols() -> None:
    frame = moves_frame([move(1, "white", "e4", "best"), move(2, "black", "f6", "mistake"),
                         move(3, "white", "Qh5", "blunder")], START)  # fmt: skip
    assert frame[["move", "san"]].values.tolist() == [["1.", "e4★"], ["1...", "f6?"],
                                                      ["2.", "Qh5??"]]  # fmt: skip
    assert frame["win_loss"].tolist() == [3.5, 3.5, 3.5]


def test_moves_frame_when_black_moves_first() -> None:
    frame = moves_frame([move(1, "black"), move(2, "white")], BLACK_TO_MOVE)
    assert frame["move"].tolist() == ["1...", "2."]
