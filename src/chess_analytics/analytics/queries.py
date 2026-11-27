"""Player analytics, computed with SQL at read time (ADR-008, ADR-026).

Deliberately PostgreSQL: CTEs, GROUPING SETS, DISTINCT ON, FILTER and regular expressions
keep each statistic one readable query. Nothing here is stored; every number is derived
from `core` and `analysis` when it is asked for.

Every query takes the same `Filters` (time class, date range), so all views of the
dashboard describe the same set of games.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from chess_analytics.analytics import weaknesses as weak
from chess_analytics.analytics.accuracy import (
    ACCURACY_DECAY,
    ACCURACY_OFFSET,
    ACCURACY_SCALE,
    HARMONIC_FLOOR,
)
from chess_analytics.classification.moves import CP_CLAMP, WIN_CURVE_SLOPE

# Chess.com names every variation ("Scotch Game 3...d6"): 660 names over 2,400 games. The
# family is the name up to its first opening keyword ("Scotch Game"): 52 families. A
# heuristic on Chess.com's naming, not an opening classification (that is planned for V2).
OPENING_FAMILY_PATTERN = r"^(.*?\m(?:Opening|Defense|Defence|Game|Gambit|Attack|System)\M)"

OpeningGrouping = Literal["family", "name"]


@dataclass(frozen=True, slots=True)
class Filters:
    time_class: str | None = None
    since: date | None = None  # inclusive, UTC
    until: date | None = None  # inclusive, UTC

    def where(self) -> tuple[str, dict[str, Any]]:
        """SQL conditions on `g` (core.games), and their parameters."""
        conditions, params = [], {}
        if self.time_class is not None:
            conditions.append("g.time_class = :time_class")
            params["time_class"] = self.time_class
        if self.since is not None:
            conditions.append("g.end_time >= :since")
            params["since"] = self.since
        if self.until is not None:
            conditions.append("g.end_time < :until_exclusive")
            params["until_exclusive"] = self.until + timedelta(days=1)
        return "".join(f" AND {c}" for c in conditions), params


ALL_GAMES = Filters()


def _record(row: Any) -> dict[str, Any]:
    games = row.games
    return {
        "games": games,
        "wins": row.wins,
        "draws": row.draws,
        "losses": row.losses,
        # Points per game as a percentage: a win is 1, a draw ½.
        "score_percent": round(Decimal(row.wins + row.draws / 2) * 100 / games, 1)
        if games
        else None,
    }


_RECORD_COLUMNS = """
    count(*) AS games,
    count(*) FILTER (WHERE gp.outcome = 'win') AS wins,
    count(*) FILTER (WHERE gp.outcome = 'draw') AS draws,
    count(*) FILTER (WHERE gp.outcome = 'loss') AS losses
"""

# For each game, the analysis to report: fresh and successful (ADR-017), the newest engine
# config if there are several, labelled by the newest classifier version stored for it.
_CHOSEN_ANALYSES = """
chosen AS (
    SELECT DISTINCT ON (ga.game_id) ga.game_id, ga.id AS analysis_id
    FROM analysis.game_analyses AS ga
    JOIN player_games AS m ON m.game_id = ga.game_id
    WHERE ga.status = 'succeeded' AND ga.moves_hash = m.moves_hash
    ORDER BY ga.game_id, ga.analysis_config_id DESC
),
-- MATERIALIZED: computed once. Inlined, Postgres estimates 1 row for this grouping and
-- then re-runs everything joined to it once per analysed game: 24 s at 418 games, growing
-- with the square of the count (found on real data, 2026-11-28).
labelled AS MATERIALIZED (
    SELECT c.game_id, c.analysis_id, max(mc.classifier_version) AS classifier_version
    FROM chosen AS c
    JOIN analysis.move_classifications AS mc ON mc.game_analysis_id = c.analysis_id
    GROUP BY c.game_id, c.analysis_id
)
"""


class PlayerAnalytics:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _player_games(self, player_id: int, filters: Filters) -> tuple[str, dict[str, Any]]:
        """CTE `player_games`: the player's games matching the filters, one row each."""
        where, params = filters.where()
        cte = f"""
player_games AS (
    SELECT g.id AS game_id, g.end_time, g.time_class, g.rated, g.ply_count, g.initial_fen,
           g.moves_hash, gp.color, gp.outcome, gp.rating_after, gp.source_accuracy
    FROM core.game_players AS gp
    JOIN core.games AS g ON g.id = gp.game_id
    WHERE gp.player_id = :player_id{where}
)"""
        return cte, {"player_id": player_id, **params}

    # --------------------------------------------------------------- overview ---

    def overview(self, player_id: int, filters: Filters = ALL_GAMES) -> dict[str, Any]:
        """Win / draw / loss: overall, by time class and by colour, in one pass."""
        where, params = filters.where()
        rows = self._session.execute(
            text(f"""
SELECT g.time_class, gp.color,
       GROUPING(g.time_class) AS all_time_classes, GROUPING(gp.color) AS all_colors,
       {_RECORD_COLUMNS}
FROM core.game_players AS gp
JOIN core.games AS g ON g.id = gp.game_id
WHERE gp.player_id = :player_id{where}
GROUP BY GROUPING SETS ((), (g.time_class), (gp.color))
"""),
            {"player_id": player_id, **params},
        ).all()
        result: dict[str, Any] = {"by_time_class": {}, "by_color": {}}
        for row in rows:
            if row.all_time_classes and row.all_colors:
                result["total"] = _record(row)
            elif not row.all_time_classes:
                result["by_time_class"][row.time_class] = _record(row)
            else:
                result["by_color"][row.color] = _record(row)
        return result

    # --------------------------------------------------------- rating history ---

    def rating_history(self, player_id: int, filters: Filters = ALL_GAMES) -> list[dict[str, Any]]:
        """The rating after the player's last rated game of each day, per time class."""
        cte, params = self._player_games(player_id, filters)
        rows = self._session.execute(
            text(f"""
WITH {cte},
daily AS (
    SELECT DISTINCT ON (time_class, (end_time AT TIME ZONE 'UTC')::date)
           time_class, (end_time AT TIME ZONE 'UTC')::date AS day, rating_after,
           count(*) OVER (PARTITION BY time_class, (end_time AT TIME ZONE 'UTC')::date) AS games
    FROM player_games
    WHERE rated AND rating_after IS NOT NULL
    ORDER BY time_class, (end_time AT TIME ZONE 'UTC')::date, end_time DESC
)
SELECT time_class, day, rating_after AS rating, games FROM daily ORDER BY time_class, day
"""),
            params,
        ).mappings()
        return [dict(row) for row in rows]

    # --------------------------------------------------------------- openings ---

    def openings(
        self,
        player_id: int,
        filters: Filters = ALL_GAMES,
        *,
        group_by: OpeningGrouping = "family",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """The most played openings per colour, with the player's results in them."""
        where, params = filters.where()
        opening = (
            "coalesce(substring(g.source_opening_name FROM :family_pattern), "
            "g.source_opening_name, 'Unknown')"
            if group_by == "family"
            else "coalesce(g.source_opening_name, 'Unknown')"
        )
        rows = self._session.execute(
            text(f"""
SELECT {opening} AS opening, gp.color, {_RECORD_COLUMNS},
       mode() WITHIN GROUP (ORDER BY g.source_eco) AS eco
FROM core.game_players AS gp
JOIN core.games AS g ON g.id = gp.game_id
WHERE gp.player_id = :player_id{where}
GROUP BY 1, gp.color
ORDER BY games DESC, opening, gp.color
LIMIT :limit
"""),
            {
                "player_id": player_id,
                "family_pattern": OPENING_FAMILY_PATTERN,
                "limit": limit,
                **params,
            },  # fmt: skip
        ).all()
        return [
            {"opening": row.opening, "color": row.color, "eco": row.eco, **_record(row)}
            for row in rows
        ]

    # ------------------------------------------------------- accuracy, errors ---

    def game_quality(self, player_id: int, filters: Filters = ALL_GAMES) -> dict[str, Any]:
        """Per analysed game: the player's accuracy and error counts. Plus coverage: how
        many of the filtered games have an analysis (ADR-004: stats must state it)."""
        cte, params = self._player_games(player_id, filters)
        rows = self._session.execute(
            text(f"""
WITH {cte},
{_CHOSEN_ANALYSES},
player_moves AS MATERIALIZED (  -- computed once, see `labelled`
    -- The player's own moves: odd plies are made by the side to move in the start position.
    SELECT l.game_id, mc.label,
           greatest(0, least(100, :acc_scale * exp(-:acc_decay * mc.eval_loss) + :acc_offset))
               AS accuracy
    FROM labelled AS l
    JOIN player_games AS m ON m.game_id = l.game_id
    JOIN analysis.move_classifications AS mc
      ON mc.game_analysis_id = l.analysis_id AND mc.classifier_version = l.classifier_version
    WHERE ((mc.ply % 2 = 1) = (split_part(m.initial_fen, ' ', 2) = 'w')) = (m.color = 'white')
)
SELECT m.game_id, m.end_time, m.time_class, m.color, m.outcome, m.source_accuracy,
       -- (mean + harmonic mean) / 2 of move accuracies, forced moves excluded (accuracy.py)
       round(((avg(pm.accuracy) FILTER (WHERE pm.label <> 'forced')
               + count(*) FILTER (WHERE pm.label <> 'forced')
                 / sum(1 / greatest(pm.accuracy, :harmonic_floor))
                       FILTER (WHERE pm.label <> 'forced')) / 2)::numeric, 1) AS accuracy,
       count(pm.label) AS moves,
       count(*) FILTER (WHERE pm.label = 'inaccuracy') AS inaccuracies,
       count(*) FILTER (WHERE pm.label = 'mistake') AS mistakes,
       count(*) FILTER (WHERE pm.label = 'blunder') AS blunders
FROM player_games AS m
JOIN labelled AS l ON l.game_id = m.game_id
LEFT JOIN player_moves AS pm ON pm.game_id = m.game_id
GROUP BY m.game_id, m.end_time, m.time_class, m.color, m.outcome, m.source_accuracy
ORDER BY m.end_time DESC, m.game_id DESC
"""),
            params
            | {
                "acc_scale": ACCURACY_SCALE,
                "acc_decay": ACCURACY_DECAY,
                "acc_offset": ACCURACY_OFFSET,
                "harmonic_floor": HARMONIC_FLOOR,
            },  # fmt: skip
        ).mappings()
        games = [dict(row) for row in rows]
        total = self._session.execute(
            text(f"WITH {cte} SELECT count(*) FROM player_games"), params
        ).scalar_one()
        return {"coverage": _coverage(len(games), total), "summary": _summary(games),
                "games": games}  # fmt: skip

    def accuracy_by_month(
        self, player_id: int, filters: Filters = ALL_GAMES
    ) -> list[dict[str, Any]]:
        """One row per calendar month (UTC) with games: its accuracy and error rates over
        the analysed games, with the same definitions as the summary, and its coverage.
        Months with games but none analysed have no accuracy (None)."""
        cte, params = self._player_games(player_id, filters)
        totals = self._session.execute(
            text(f"""
WITH {cte}
SELECT date_trunc('month', end_time AT TIME ZONE 'UTC')::date AS month, count(*) AS games
FROM player_games GROUP BY 1 ORDER BY 1
"""),
            params,
        ).all()
        analysed: dict[date, list[dict[str, Any]]] = {}
        for game in self.game_quality(player_id, filters)["games"]:
            analysed.setdefault(_utc_month(game["end_time"]), []).append(game)
        return [
            {
                "month": row.month,
                **_coverage(len(analysed.get(row.month, [])), row.games),
                **_summary(analysed.get(row.month, [])),
            }
            for row in totals
        ]

    # ------------------------------------------------------------- weaknesses ---

    def weaknesses(self, player_id: int, filters: Filters = ALL_GAMES) -> dict[str, Any]:
        """Where errors happen (ADR-030, definitions in analytics/weaknesses.py): the
        player's analysed moves by game phase and by position, and games by opponent
        strength. Moves exclude forced ones, as for accuracy."""
        cte, params = self._player_games(player_id, filters)
        rows = self._session.execute(
            text(f"""
WITH {cte},
{_CHOSEN_ANALYSES},
player_moves AS (
    SELECT mc.ply, mc.label, m.color, m.initial_fen,
           greatest(0, least(100, :acc_scale * exp(-:acc_decay * mc.eval_loss) + :acc_offset))
               AS accuracy,
           split_part(coalesce(prev.fen_after, m.initial_fen), ' ', 1) AS board_before,
           pe.score_cp, pe.mate_in
    FROM labelled AS l
    JOIN player_games AS m ON m.game_id = l.game_id
    JOIN analysis.move_classifications AS mc
      ON mc.game_analysis_id = l.analysis_id AND mc.classifier_version = l.classifier_version
    -- The position before the move: its board, and the engine's view of it.
    LEFT JOIN core.game_moves AS prev ON prev.game_id = l.game_id AND prev.ply = mc.ply - 1
    JOIN analysis.position_evaluations AS pe
      ON pe.game_analysis_id = l.analysis_id AND pe.ply = mc.ply - 1 AND pe.pv_rank = 1
    WHERE ((mc.ply % 2 = 1) = (split_part(m.initial_fen, ' ', 2) = 'w')) = (m.color = 'white')
      AND mc.label <> 'forced'
),
bucketed AS (
    SELECT label, accuracy,
           CASE
               WHEN length(board_before)
                    - length(regexp_replace(board_before, '[QRBNqrbn]', '', 'g'))
                    <= :endgame_pieces THEN 'endgame'
               WHEN (ply + CASE WHEN split_part(initial_fen, ' ', 2) = 'w' THEN 1 ELSE 2 END) / 2
                    <= :opening_moves THEN 'opening'
               ELSE 'middlegame'
           END AS phase,
           CASE
               WHEN mover_win >= :winning_from THEN 'winning'
               WHEN mover_win <= :losing_up_to THEN 'losing'
               ELSE 'equal'
           END AS position
    FROM (
        SELECT *,
               CASE WHEN color = 'white' THEN white_win ELSE 100 - white_win END AS mover_win
        FROM (
            SELECT *,
                   CASE
                       WHEN mate_in > 0 THEN 100.0
                       WHEN mate_in < 0 THEN 0.0
                       -- mate_in 0 can't precede a move (the game would be over)
                       ELSE 50 + 50 * (2 / (1 + exp(-:slope
                                 * greatest(-:cp_clamp, least(:cp_clamp, score_cp)))) - 1)
                   END AS white_win
            FROM player_moves
        ) AS w
    ) AS m
)
SELECT GROUPING(phase) = 1 AS by_position, coalesce(phase, position) AS bucket,
       count(*) AS moves,
       count(*) FILTER (WHERE label = 'inaccuracy') AS inaccuracies,
       count(*) FILTER (WHERE label = 'mistake') AS mistakes,
       count(*) FILTER (WHERE label = 'blunder') AS blunders,
       round(((avg(accuracy) + count(*) / sum(1 / greatest(accuracy, :harmonic_floor))) / 2
             )::numeric, 1) AS accuracy
FROM bucketed
GROUP BY GROUPING SETS ((phase), (position))
"""),
            params
            | {
                "acc_scale": ACCURACY_SCALE,
                "acc_decay": ACCURACY_DECAY,
                "acc_offset": ACCURACY_OFFSET,
                "harmonic_floor": HARMONIC_FLOOR,
                "endgame_pieces": weak.ENDGAME_MAX_PIECES,
                "opening_moves": weak.OPENING_MOVES,
                "winning_from": weak.WINNING_FROM,
                "losing_up_to": weak.LOSING_UP_TO,
                "slope": WIN_CURVE_SLOPE,
                "cp_clamp": CP_CLAMP,
            },  # fmt: skip
        ).all()
        by_phase = {r.bucket: r for r in rows if not r.by_position}
        by_position = {r.bucket: r for r in rows if r.by_position}
        return {
            "by_phase": [_move_bucket("phase", p, by_phase.get(p)) for p in weak.PHASES],
            "by_position": [
                _move_bucket("position", p, by_position.get(p)) for p in weak.POSITIONS
            ],
            "by_opponent": self._by_opponent(player_id, filters, cte, params),
        }

    def _by_opponent(
        self, player_id: int, filters: Filters, cte: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Results (all games) and accuracy (analysed games) per opponent-strength band,
        on estimated pre-game ratings (analytics/weaknesses.py)."""
        rows = self._session.execute(
            text(f"""
WITH previous AS (
    -- Over ALL the player's games, before any filter: the game before a filtered-in
    -- game may itself be filtered out (e.g. by date), and still gives the rating before.
    SELECT gp.game_id,
           lag(gp.rating_after) OVER (PARTITION BY g.time_class ORDER BY g.end_time, g.id)
               AS rating_before
    FROM core.game_players AS gp
    JOIN core.games AS g ON g.id = gp.game_id
    WHERE gp.player_id = :player_id
),
{cte}
SELECT m.game_id, m.outcome,
       (opp.rating_after + (m.rating_after - p.rating_before)) - p.rating_before AS difference
FROM player_games AS m
JOIN previous AS p ON p.game_id = m.game_id
JOIN core.game_players AS opp ON opp.game_id = m.game_id AND opp.color <> m.color
WHERE p.rating_before IS NOT NULL AND m.rating_after IS NOT NULL
  AND opp.rating_after IS NOT NULL
"""),
            params,
        ).all()
        band_of = {row.game_id: weak.rating_band(row.difference) for row in rows}
        quality = self.game_quality(player_id, filters)["games"]
        result = []
        for band, _ in weak.RATING_BANDS:
            outcomes = [row.outcome for row in rows if band_of[row.game_id] == band]
            record = _Record(
                games=len(outcomes),
                wins=outcomes.count("win"),
                draws=outcomes.count("draw"),
                losses=outcomes.count("loss"),
            )
            analysed = [g for g in quality if band_of.get(g["game_id"]) == band]
            result.append({
                "band": band,
                **_record(record),
                "analysed_games": len(analysed),
                **_summary(analysed),
            })  # fmt: skip
        return result


@dataclass(frozen=True, slots=True)
class _Record:
    games: int
    wins: int
    draws: int
    losses: int


def _move_bucket(key: str, name: str, row: Any) -> dict[str, Any]:
    """Error rates per 100 of the player's moves in this bucket (buckets differ in size)."""
    moves = row.moves if row is not None else 0

    def per_100(count: int) -> Decimal | None:
        return round(Decimal(count) * 100 / moves, 1) if moves else None

    return {
        key: name,
        "moves": moves,
        "accuracy": row.accuracy if row is not None else None,
        "inaccuracies_per_100": per_100(row.inaccuracies) if row else None,
        "mistakes_per_100": per_100(row.mistakes) if row else None,
        "blunders_per_100": per_100(row.blunders) if row else None,
    }


def _utc_month(moment: datetime) -> date:
    utc = moment.astimezone(UTC)
    return date(utc.year, utc.month, 1)


def _coverage(analysed: int, total: int) -> dict[str, Any]:
    return {
        "analysed_games": analysed,
        "total_games": total,
        "percent": round(Decimal(analysed) * 100 / total, 1) if total else None,
    }


def _summary(games: list[dict[str, Any]]) -> dict[str, Any]:
    """Averages over analysed games: each game counts once, however long it was."""
    accuracies = [g["accuracy"] for g in games if g["accuracy"] is not None]
    n = len(games)

    def per_game(key: str) -> Decimal | None:
        return round(Decimal(sum(g[key] for g in games)) / n, 2) if n else None

    return {
        "accuracy": round(sum(accuracies) / len(accuracies), 1) if accuracies else None,
        "inaccuracies_per_game": per_game("inaccuracies"),
        "mistakes_per_game": per_game("mistakes"),
        "blunders_per_game": per_game("blunders"),
    }
