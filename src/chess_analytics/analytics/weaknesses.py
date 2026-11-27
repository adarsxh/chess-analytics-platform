"""Where a player's errors happen (ADR-030): by game phase, by position, by opponent.

Definitions (agreed 2026-11-28):

- **Game phase** of a move, from the position before it:
    endgame     6 or fewer queens, rooks, bishops and knights on the board (both sides;
                Lichess's endgame rule), checked first;
    opening     the player's first 10 moves;
    middlegame  everything else.
- **Position** before the move, as the mover's win chance (the classifier's curve,
  ADR-024): winning ≥ 70 %, losing ≤ 30 %, equal in between.
- **Opponent strength**: the opponent's rating minus the player's, both *before* the game,
  in 5 bands. Chess.com reports only post-game ratings, which depend on the result (a win
  moves the gap ~20 points towards "weaker opponent"), so pre-game ratings are estimated:
  the player's is their rating after their previous game in the same time class, and the
  opponent's is their post-game rating moved back by the player's change (Elo is close to
  zero-sum). A player's first game in a time class has no estimate and is left out.
  Matchmaking keeps most gaps small (98 % within ±50 for the main user), hence narrow bands.

Phase and position are per move (forced moves excluded, as for accuracy); opponent bands
are per game. Computed in SQL at read time (analytics/queries.py); the functions below
define the same rules and are what the tests check SQL against.
"""

from chess_analytics.classification.moves import white_win_percent
from chess_analytics.engine.base import Score

OPENING_MOVES = 10
ENDGAME_MAX_PIECES = 6  # queens, rooks, bishops and knights of both sides
WINNING_FROM = 70.0  # mover's win %, inclusive
LOSING_UP_TO = 30.0  # inclusive

PHASES = ("opening", "middlegame", "endgame")
POSITIONS = ("winning", "equal", "losing")
# (name, upper bound of opponent − player before the game, inclusive); the last is unbounded.
RATING_BANDS = (
    ("much_lower", -25),
    ("lower", -10),
    ("similar", 9),
    ("higher", 24),
    ("much_higher", None),
)


def pieces(fen: str) -> int:
    """Queens, rooks, bishops and knights on the board (kings and pawns not counted)."""
    return sum(fen.split()[0].count(piece) for piece in "QRBNqrbn")


def move_number(ply: int, white_first: bool) -> int:
    """Full-move number of a ply: ply 1 is move 1 (of White, or of Black if Black starts)."""
    return (ply + (1 if white_first else 2)) // 2


def phase(fen_before: str, ply: int, white_first: bool) -> str:
    if pieces(fen_before) <= ENDGAME_MAX_PIECES:
        return "endgame"
    if move_number(ply, white_first) <= OPENING_MOVES:
        return "opening"
    return "middlegame"


def position(mover_win_percent: float) -> str:
    if mover_win_percent >= WINNING_FROM:
        return "winning"
    if mover_win_percent <= LOSING_UP_TO:
        return "losing"
    return "equal"


def mover_win_percent(score_cp: int | None, mate_in: int | None, mover_is_white: bool) -> float:
    """The mover's win chance before the move, from White's-view score or signed mate."""
    white = white_win_percent(Score(cp=score_cp, mate=mate_in), white_to_move=mover_is_white)
    return white if mover_is_white else 100.0 - white


def pre_game_difference(player_after: int, player_before: int, opponent_after: int) -> int:
    """Opponent minus player, before the game: the player's change is undone on both."""
    change = player_after - player_before
    return (opponent_after + change) - player_before


def rating_band(opponent_minus_player: int) -> str:
    for name, upper in RATING_BANDS:
        if upper is None or opponent_minus_player <= upper:
            return name
    raise AssertionError("unreachable: the last band is unbounded")
