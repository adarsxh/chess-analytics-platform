"""Weaknesses in SQL (ADR-030) against the Python reference definitions."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.analytics import weaknesses as weak
from chess_analytics.analytics.accuracy import game_accuracy
from chess_analytics.analytics.queries import Filters, PlayerAnalytics
from chess_analytics.domain.game import Color, GameResult, Outcome, ParsedMove, ParsedPlayer
from chess_analytics.engine.base import EngineIdentity, EngineResult, EngineSettings, Score
from chess_analytics.storage.models.core import Game
from chess_analytics.storage.repositories.analysis import AnalysisRepository
from chess_analytics.storage.repositories.queries import GameQueries

from .factories import store_game

pytestmark = pytest.mark.integration

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
FULL = START.split()[0]  # 14 pieces
FEW = "r3k2r/2n5/8/8/8/2B5/8/R3K2R"  # exactly 6 pieces: the largest endgame
ME = ParsedPlayer(Color.WHITE, "uuid-me", "Me", 1500, "win", Outcome.WIN, None)


def opponent(rating: int) -> ParsedPlayer:
    return ParsedPlayer(Color.BLACK, f"uuid-{rating}", f"opp{rating}", rating, "resigned",
                        Outcome.LOSS, None)  # fmt: skip


def store(session_factory, *, boards: list[str], me_white=True, me_rating=1500,
          opp_rating=1500, day=0) -> int:  # fmt: skip
    """A game whose board after ply i is boards[i - 1] (moves themselves are dummies)."""
    moves = tuple(
        ParsedMove(
            ply=i,
            san=f"m{i}",
            uci="a2a3",
            fen_after=f"{board} w - - 0 1",
            clock_ms=None,
            time_spent_ms=None,
        )  # fmt: skip
        for i, board in enumerate(boards, start=1)
    )
    me = replace(ME, rating_after=me_rating)
    them = opponent(opp_rating)
    if not me_white:
        me = replace(me, color=Color.BLACK)
        them = replace(them, color=Color.WHITE)
    return store_game(
        session_factory,
        moves=moves,
        white=me if me_white else them,
        black=them if me_white else me,
        result=GameResult.WHITE_WIN,
        end_time=datetime(2026, 10, 1, 12, tzinfo=UTC) + timedelta(days=day),
    )


def analyse(session_factory, game_id: int, scores: list[Score], labels: list[tuple[str, str]]):
    """Evaluations for plies 0..n (White's view) and (label, win-% loss) for plies 1..n."""
    with session_factory.begin() as session:
        repo = AnalysisRepository(session)
        config = repo.get_or_create_config(EngineIdentity("fake", "1"),
                                           EngineSettings(nodes_per_position=1000))  # fmt: skip
        analysis_id = repo.mark_running(game_id, config.id, session.get(Game, game_id).moves_hash)
        repo.save_evaluations(
            analysis_id, [EngineResult(score, "a2a3", ("a2a3",), 1, 1) for score in scores]
        )
        repo.save_classifications(
            analysis_id, 1, [(ply, lab, Decimal(loss)) for ply, (lab, loss) in enumerate(labels, 1)]
        )


def expected_buckets(games) -> tuple[dict, dict]:
    """The Python reference: bucket each of my moves, then the same aggregates as SQL."""
    phases, positions = {}, {}
    for boards, me_white, scores, labels in games:
        for ply, (label, loss) in enumerate(labels, start=1):
            if (ply % 2 == 1) != me_white or label == "forced":
                continue
            before = START if ply == 1 else f"{boards[ply - 2]} w - - 0 1"
            score = scores[ply - 1]
            win = weak.mover_win_percent(score.cp, score.mate, me_white)
            move = (label, float(loss))
            phases.setdefault(weak.phase(before, ply, True), []).append(move)
            positions.setdefault(weak.position(win), []).append(move)
    return phases, positions


def check(rows: list[dict], key: str, expected: dict) -> None:
    for row in rows:
        moves = expected.get(row[key], [])
        assert row["moves"] == len(moves), row
        if not moves:
            assert row["accuracy"] is None and row["blunders_per_100"] is None
            continue
        accuracy = game_accuracy(loss for _, loss in moves)
        assert row["accuracy"] == round(Decimal(accuracy), 1), row
        for kind, label in (("blunders", "blunder"), ("mistakes", "mistake"),
                            ("inaccuracies", "inaccuracy")):  # fmt: skip
            count = sum(1 for lab, _ in moves if lab == label)
            assert row[f"{kind}_per_100"] == round(Decimal(count) * 100 / len(moves), 1), row


def player_id(session_factory, username="Me") -> int:
    with session_factory() as session:
        return GameQueries(session).find_player(platform="chesscom", username=username).id


@pytest.fixture
def stats(session_factory: sessionmaker[Session]):
    with session_factory() as session:
        yield PlayerAnalytics(session)


def test_moves_by_phase_and_position_match_the_definitions(session_factory, stats) -> None:
    # 24 plies as White: my moves 1–10 in the opening, move 11 (ply 21) in the middlegame,
    # move 12 (ply 23) in an endgame (the board thins out after ply 21).
    white_boards = [FULL] * 21 + [FEW] * 3
    cp = [0, 0, 300, 300, -300, -300, 0, 0, 120, 120, -120, 900, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
          0, 0, 0]  # fmt: skip
    white_scores = [Score(cp=c) for c in cp]
    white_scores[22] = Score(mate=2)  # before ply 23: I mate in 2
    white_labels = [("best", "0"), ("good", "3")] * 12
    white_labels[0] = ("forced", "0")
    white_labels[4] = ("blunder", "35")  # ply 5, losing position (-300 for me)
    white_labels[10] = ("mistake", "22")  # ply 11, equal (-120 ≈ 37 %)
    white_labels[20] = ("inaccuracy", "12")  # ply 21, middlegame
    white_labels[22] = ("blunder", "60")  # ply 23, endgame, winning (mate)
    # As Black: +300 for White before my move is a losing position for me.
    # 22 plies as Black: my 10th move (ply 20) is still the opening, my 11th (ply 22) isn't.
    black_boards = [FULL] * 22
    black_scores = [Score(cp=0), Score(cp=300), Score(cp=0), Score(cp=-300)] + [Score(cp=0)] * 19
    black_labels = [("best", "0"), ("blunder", "40"), ("best", "0"), ("mistake", "25")]
    black_labels += [("good", "4")] * 15 + [("mistake", "21"), ("best", "0"), ("blunder", "33")]
    # As Black, −300 for White before ply 4 is winning for me.
    store_white = store(session_factory, boards=white_boards)
    store_black = store(session_factory, boards=black_boards, me_white=False, day=1)
    analyse(session_factory, store_white, white_scores, white_labels)
    analyse(session_factory, store_black, black_scores, black_labels)

    result = stats.weaknesses(player_id(session_factory))

    phases, positions = expected_buckets([
        (white_boards, True, white_scores, white_labels),
        (black_boards, False, black_scores, black_labels),
    ])  # fmt: skip
    assert [r["phase"] for r in result["by_phase"]] == ["opening", "middlegame", "endgame"]
    assert [r["position"] for r in result["by_position"]] == ["winning", "equal", "losing"]
    # Opening: 9 of my White moves (the forced one is left out) + 10 Black; middlegame: one each.
    assert [r["moves"] for r in result["by_phase"]] == [9 + 10, 1 + 1, 1]
    check(result["by_phase"], "phase", phases)
    check(result["by_position"], "position", positions)
    losing = next(r for r in result["by_position"] if r["position"] == "losing")
    # Ply 5 as White (−300) and ply 2 as Black (+300 for White); before Black's ply 4 it's
    # −300 for White, which is winning for me as Black.
    assert losing["moves"] == 2


def test_opponent_bands_use_estimated_pre_game_ratings(session_factory, stats) -> None:
    # My ratings after each game; the first has no previous game, so no estimate.
    mine = [1500, 1508, 1500, 1509, 1501, 1510]
    wanted = [None, -30, -12, 0, 15, 40]  # pre-game gap (opponent − me) for games 1..5
    games = []
    for i, (after, gap) in enumerate(zip(mine, wanted, strict=True)):
        before = mine[i - 1] if i else after
        opponent_after = after if gap is None else gap + before - (after - before)
        assert gap is None or weak.pre_game_difference(after, before, opponent_after) == gap
        games.append(store(session_factory, boards=[FULL] * 4, me_rating=after,
                           opp_rating=opponent_after, day=i))  # fmt: skip
    analyse(session_factory, games[1], [Score(cp=0)] * 5,
            [("best", "0"), ("best", "0"), ("blunder", "40"), ("best", "0")])  # fmt: skip
    me = player_id(session_factory)

    bands = {row["band"]: row for row in stats.weaknesses(me)["by_opponent"]}

    assert list(bands) == ["much_lower", "lower", "similar", "higher", "much_higher"]
    assert [row["games"] for row in bands.values()] == [1, 1, 1, 1, 1]  # the first: left out
    assert bands["much_lower"]["score_percent"] == Decimal("100.0")
    assert (bands["much_lower"]["analysed_games"], bands["much_lower"]["blunders_per_game"]) == (
        1, Decimal("1.00"),
    )  # fmt: skip
    assert bands["much_lower"]["accuracy"] == round(Decimal(game_accuracy([0, 40])), 1)
    assert (bands["similar"]["analysed_games"], bands["similar"]["accuracy"]) == (0, None)

    # From day 4 on: games 4 and 5 only. Game 4's previous game (day 3) is filtered out but
    # still gives my rating before game 4, so game 4 keeps its estimate.
    late = Filters(since=date(2026, 10, 5))
    assert [r["games"] for r in stats.weaknesses(me, late)["by_opponent"]] == [0, 0, 0, 1, 1]


def test_nothing_analysed_and_filters(session_factory, stats) -> None:
    store(session_factory, boards=[FULL] * 4)  # first game: no pre-game rating
    store(session_factory, boards=[FULL] * 4, day=1)
    me = player_id(session_factory)

    result = stats.weaknesses(me)
    assert [r["moves"] for r in result["by_phase"] + result["by_position"]] == [0] * 6
    assert sum(r["games"] for r in result["by_opponent"]) == 1

    none = stats.weaknesses(me, Filters(time_class="bullet"))
    assert sum(r["games"] for r in none["by_opponent"]) == 0
    assert all(r["score_percent"] is None for r in none["by_opponent"])
