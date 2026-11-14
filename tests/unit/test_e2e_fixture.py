"""The e2e stub's synthetic games must pass the strict mapper; otherwise the e2e test
would fail for a confusing reason. Cheap to check here, without Docker."""

import json
from pathlib import Path

from chess_analytics.ingestion.chesscom.mapping import to_parsed_game

FIXTURE = Path(__file__).parents[1] / "e2e" / "chesscom_fixture.json"


def test_e2e_fixture_games_are_valid_chesscom_games() -> None:
    games = json.loads(FIXTURE.read_text())["games"]["2026/10"]
    parsed = [to_parsed_game(game) for game in games]
    assert [g.result for g in parsed] == ["black_win", "white_win"]
