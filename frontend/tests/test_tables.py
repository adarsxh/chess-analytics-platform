"""API JSON → DataFrames."""

from dashboard.tables import accuracy_frame, openings_frame, rating_frame, results_frame

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
