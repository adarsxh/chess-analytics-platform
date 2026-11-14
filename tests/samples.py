"""Synthetic platform payloads shared by unit and integration tests.

Shaped exactly like real Chess.com archive games (verified in Phase 2/4 profiling), but
every value is made up: no real usernames or game ids are committed.
"""

from typing import Any

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
# Fool's mate: Black mates on move 2.
FINAL = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq -"  # 4 fields, like Chess.com

PGN = """[Event "Live Chess"]
[Result "0-1"]
[ECO "C00"]
[ECOUrl "https://www.chess.com/openings/Kings-Pawn-Opening"]
[UTCDate "2026.10.02"]
[UTCTime "18:30:05"]
[TimeControl "600"]
[Termination "player_b won by checkmate"]

1. f3 {[%clk 0:09:58]} 1... e5 {[%clk 0:09:59]}
2. g4 {[%clk 0:09:50.5]} 2... Qh4# {[%clk 0:09:57.2]} 0-1
"""


def chesscom_game(**overrides: Any) -> dict[str, Any]:
    game = {
        "uuid": "11111111-2222-3333-4444-555555555555",
        "url": "https://www.chess.com/game/live/1",
        "pgn": PGN,
        "time_control": "600",
        "end_time": 1_790_015_405,
        "rated": True,
        "fen": FINAL,
        "initial_setup": START,
        "time_class": "rapid",
        "rules": "chess",
        "eco": "https://www.chess.com/openings/Kings-Pawn-Opening-1...e5",
        "accuracies": {"white": 12.3, "black": 98.76},
        "white": {"username": "player_a", "uuid": "uuid-a", "rating": 1490, "result": "checkmated"},
        "black": {"username": "player_b", "uuid": "uuid-b", "rating": 1510, "result": "win"},
    }
    return game | overrides
