"""Player analytics SQL against the real schema, on small hand-built histories."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.analytics.accuracy import game_accuracy
from chess_analytics.analytics.queries import Filters, PlayerAnalytics
from chess_analytics.domain.game import Color, GameResult, Outcome, ParsedPlayer, TimeClass
from chess_analytics.engine.base import EngineIdentity, EngineResult, EngineSettings, Score
from chess_analytics.storage.models.analysis import GameAnalysis
from chess_analytics.storage.models.core import Game
from chess_analytics.storage.repositories.analysis import AnalysisRepository
from chess_analytics.storage.repositories.queries import GameQueries

from .factories import store_game

pytestmark = pytest.mark.integration

ME = ParsedPlayer(Color.WHITE, "uuid-me", "Me", 1500, "win", Outcome.WIN, None)
RESULTS = {  # my outcome → (my result code, the game result if I am White)
    Outcome.WIN: ("win", GameResult.WHITE_WIN),
    Outcome.LOSS: ("resigned", GameResult.BLACK_WIN),
    Outcome.DRAW: ("agreed", GameResult.DRAW),
}
OPPOSITE = {Outcome.WIN: Outcome.LOSS, Outcome.LOSS: Outcome.WIN, Outcome.DRAW: Outcome.DRAW}
FLIP = {GameResult.WHITE_WIN: GameResult.BLACK_WIN, GameResult.BLACK_WIN: GameResult.WHITE_WIN,
        GameResult.DRAW: GameResult.DRAW}  # fmt: skip


def play(
    session_factory,
    *,
    white: bool = True,
    outcome: Outcome = Outcome.WIN,
    when: datetime = datetime(2026, 10, 1, 12, tzinfo=UTC),
    time_class: TimeClass = TimeClass.RAPID,
    rating: int | None = 1500,
    rated: bool = True,
    opening: str | None = "Kings Pawn Opening",
    accuracy: Decimal | None = None,
    opponent: str = "Opponent",
) -> int:
    """Store a game "Me" played against `opponent`; returns its id."""
    code, result = RESULTS[outcome]
    me = replace(ME, color=Color.WHITE if white else Color.BLACK, rating_after=rating,
                 result_code=code, outcome=outcome, source_accuracy=accuracy)  # fmt: skip
    them = ParsedPlayer(Color.BLACK if white else Color.WHITE, f"uuid-{opponent}", opponent,
                        1400, "x", OPPOSITE[outcome], None)  # fmt: skip
    return store_game(
        session_factory,
        white=me if white else them,
        black=them if white else me,
        result=result if white else FLIP[result],
        end_time=when,
        time_class=time_class,
        rated=rated,
        source_opening_name=opening,
    )


def me_id(session_factory) -> int:
    with session_factory() as session:
        return GameQueries(session).find_player(platform="chesscom", username="Me").id


@pytest.fixture
def stats(session_factory: sessionmaker[Session]):
    with session_factory() as session:
        yield PlayerAnalytics(session)


def record(games, wins, draws, losses, score) -> dict:
    return {"games": games, "wins": wins, "draws": draws, "losses": losses,
            "score_percent": None if score is None else Decimal(score)}  # fmt: skip


# --------------------------------------------------------------- overview ---


def test_overview_by_time_class_and_colour(session_factory, stats) -> None:
    play(session_factory, white=True, outcome=Outcome.WIN)
    play(session_factory, white=False, outcome=Outcome.LOSS)
    play(session_factory, white=True, outcome=Outcome.DRAW, time_class=TimeClass.BLITZ)
    play(session_factory, white=False, outcome=Outcome.WIN)
    store_game(session_factory)  # someone else's game: never counted

    overview = stats.overview(me_id(session_factory))

    assert overview["total"] == record(4, 2, 1, 1, "62.5")
    assert overview["by_time_class"] == {
        "rapid": record(3, 2, 0, 1, "66.7"),
        "blitz": record(1, 0, 1, 0, "50.0"),
    }
    assert overview["by_color"] == {
        "white": record(2, 1, 1, 0, "75.0"),
        "black": record(2, 1, 0, 1, "50.0"),
    }


def test_filters_by_time_class_and_inclusive_date_range(session_factory, stats) -> None:
    for day in (1, 2, 3):  # one game per day, late in the (UTC) day
        play(session_factory, when=datetime(2026, 10, day, 23, 59, tzinfo=UTC))
    play(session_factory, when=datetime(2026, 10, 2, 12, tzinfo=UTC), time_class=TimeClass.BLITZ)
    player = me_id(session_factory)

    def games(**filters) -> int:
        return stats.overview(player, Filters(**filters))["total"]["games"]

    assert games(since=date(2026, 10, 2)) == 3
    assert games(until=date(2026, 10, 2)) == 3  # the whole of Oct 2 is included
    assert games(since=date(2026, 10, 2), until=date(2026, 10, 2)) == 2
    assert games(time_class="blitz") == 1
    assert games(time_class="rapid", since=date(2026, 10, 2)) == 2


def test_no_games_in_range(session_factory, stats) -> None:
    play(session_factory)
    overview = stats.overview(me_id(session_factory), Filters(since=date(2030, 1, 1)))
    assert overview == {"total": record(0, 0, 0, 0, None), "by_time_class": {}, "by_color": {}}


# --------------------------------------------------------- rating history ---


def test_rating_history_keeps_the_last_rated_game_of_each_day(session_factory, stats) -> None:
    day1, day2 = datetime(2026, 10, 1, 9, tzinfo=UTC), datetime(2026, 10, 2, 9, tzinfo=UTC)
    play(session_factory, when=day1, rating=1500)
    play(session_factory, when=day1.replace(hour=20), rating=1512)  # last of the day
    play(session_factory, when=day1.replace(hour=22), rating=None)  # no rating: skipped
    play(session_factory, when=day2, rating=1530, rated=False)  # unrated: skipped
    play(session_factory, when=day2, rating=1100, time_class=TimeClass.BLITZ)

    history = stats.rating_history(me_id(session_factory))

    assert history == [
        {"time_class": "blitz", "day": date(2026, 10, 2), "rating": 1100, "games": 1},
        {"time_class": "rapid", "day": date(2026, 10, 1), "rating": 1512, "games": 2},
    ]


# --------------------------------------------------------------- openings ---


def test_openings_by_family_and_by_name(session_factory, stats) -> None:
    play(session_factory, opening="Scotch Game", outcome=Outcome.WIN)
    play(session_factory, opening="Scotch Game 3...d6", outcome=Outcome.LOSS)
    play(session_factory, opening="Scotch Game 3...d6", outcome=Outcome.WIN)
    play(session_factory, opening="Caro Kann Defense Advance Variation", white=False)
    play(session_factory, opening=None, white=False)
    player = me_id(session_factory)

    families = stats.openings(player)
    by_name = stats.openings(player, group_by="name", limit=2)

    assert [(o["opening"], o["color"], o["games"], o["score_percent"]) for o in families] == [
        ("Scotch Game", "white", 3, Decimal("66.7")),
        ("Caro Kann Defense", "black", 1, Decimal("100.0")),
        ("Unknown", "black", 1, Decimal("100.0")),
    ]
    assert families[0]["eco"] == "C20"
    assert [(o["opening"], o["games"]) for o in by_name] == [
        ("Scotch Game 3...d6", 2),
        ("Caro Kann Defense Advance Variation", 1),
    ]


# ------------------------------------------------------- accuracy, errors ---


def analyse(session_factory, game_id: int, labels: list[tuple[str, str]], *, version: int = 1,
            engine: str = "fake") -> None:  # fmt: skip
    """Store an analysis whose classifications are `labels`: (label, loss) for plies 1..n.
    Every game here is fool's mate (4 plies); version 1 also stores the evaluations."""
    with session_factory.begin() as session:
        repo = AnalysisRepository(session)
        config = repo.get_or_create_config(EngineIdentity(engine, "1"),
                                           EngineSettings(nodes_per_position=1000))  # fmt: skip
        if version == 1:
            analysis_id = repo.mark_running(
                game_id, config.id, session.get(Game, game_id).moves_hash
            )
            dummy = EngineResult(Score(cp=0), "e2e4", ("e2e4",), 1, 1)
            repo.save_evaluations(analysis_id, [dummy] * (len(labels) + 1))
        else:  # a new classifier re-labels the existing analysis (no engine run)
            analysis_id = repo.get_analysis(game_id, config.id).id
        rows = [(ply, label, Decimal(loss)) for ply, (label, loss) in enumerate(labels, 1)]
        repo.save_classifications(analysis_id, version, rows)


def test_game_quality_counts_only_the_players_own_moves(session_factory, stats) -> None:
    as_white = play(session_factory, white=True, accuracy=Decimal("70.5"))
    as_black = play(session_factory, white=False, when=datetime(2026, 10, 2, tzinfo=UTC))
    # Fool's mate has 4 plies: White plays 1 and 3, Black plays 2 and 4.
    moves = [("best", "0"), ("blunder", "45.5"), ("inaccuracy", "12"), ("mistake", "25")]
    analyse(session_factory, as_white, moves)
    analyse(session_factory, as_black, moves)

    quality = stats.game_quality(me_id(session_factory))

    newest, older = quality["games"]
    assert (newest["game_id"], newest["color"]) == (as_black, "black")
    assert (newest["moves"], newest["blunders"], newest["mistakes"]) == (2, 1, 1)
    assert newest["accuracy"] == round(Decimal(game_accuracy([45.5, 25])), 1)
    assert (older["moves"], older["inaccuracies"], older["blunders"]) == (2, 1, 0)
    assert older["accuracy"] == round(Decimal(game_accuracy([0, 12])), 1)
    assert older["source_accuracy"] == Decimal("70.50")  # Chess.com's, for comparison
    assert quality["summary"] == {
        "accuracy": round((newest["accuracy"] + older["accuracy"]) / 2, 1),
        "inaccuracies_per_game": Decimal("0.50"),
        "mistakes_per_game": Decimal("0.50"),
        "blunders_per_game": Decimal("0.50"),
    }


def test_forced_moves_count_as_moves_but_not_for_accuracy(session_factory, stats) -> None:
    game = play(session_factory)
    analyse(session_factory, game, [("forced", "0"), ("best", "0"), ("good", "8"), ("best", "0")])

    (row,) = stats.game_quality(me_id(session_factory))["games"]

    assert row["moves"] == 2
    assert row["accuracy"] == round(Decimal(game_accuracy([8])), 1)


def test_coverage_counts_only_fresh_successful_analyses(session_factory, stats) -> None:
    analysed, stale, failed, _never = (play(session_factory) for _ in range(4))
    for game in (analysed, stale, failed):
        analyse(session_factory, game, [("best", "0")] * 4)
    with session_factory.begin() as session:
        session.get(Game, stale).moves_hash = "re-processed"  # moves changed since
        failed_analysis = session.scalars(
            select(GameAnalysis.id).where(GameAnalysis.game_id == failed)
        ).one()
        AnalysisRepository(session).mark_failed(failed_analysis, "engine crashed")

    quality = stats.game_quality(me_id(session_factory))

    assert [g["game_id"] for g in quality["games"]] == [analysed]
    assert quality["coverage"] == {"analysed_games": 1, "total_games": 4,
                                   "percent": Decimal("25.0")}  # fmt: skip


def test_the_newest_classifier_version_is_used(session_factory, stats) -> None:
    game = play(session_factory)
    analyse(session_factory, game, [("best", "0")] * 4)
    analyse(session_factory, game, [("blunder", "40"), ("best", "0")] * 2, version=2)

    (row,) = stats.game_quality(me_id(session_factory))["games"]

    assert row["blunders"] == 2


def test_no_analysed_games(session_factory, stats) -> None:
    play(session_factory)
    quality = stats.game_quality(me_id(session_factory))
    assert quality["games"] == []
    assert quality["coverage"]["analysed_games"] == 0
    assert quality["summary"] == {"accuracy": None, "inaccuracies_per_game": None,
                                  "mistakes_per_game": None, "blunders_per_game": None}  # fmt: skip
