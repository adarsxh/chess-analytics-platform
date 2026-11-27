"""The `analysis` layer: engine configs, game analyses, evaluations and classifications.

DATA_MODEL.md §4.3, ADR-017:
- one `game_analyses` row per (game, config); re-running the same config replaces its
  evaluations in place, a different config adds a row and keeps the old one;
- classifications are keyed by classifier version, so re-classifying adds rows and never
  needs the engine;
- an analysis is *fresh* while its moves_hash equals the game's current moves_hash.

Like every repository, this one never commits: the service owns the transaction.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from chess_analytics.engine.base import (
    EngineIdentity,
    EngineResult,
    EngineSettings,
    Score,
    config_hash,
)
from chess_analytics.storage.models.analysis import (
    AnalysisConfig,
    GameAnalysis,
    MoveClassification,
    PositionEvaluation,
)
from chess_analytics.storage.models.core import Game, GameMove, GamePlayer

# (ply, label, eval_loss): classifier output as plain values, so storage does not depend
# on the classification package (ARCHITECTURE §3).
ClassificationRow = tuple[int, str, Decimal]


@dataclass(frozen=True, slots=True)
class GameToAnalyse:
    game_id: int
    initial_fen: str
    moves_uci: tuple[str, ...]
    moves_hash: str


class AnalysisRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ---------------------------------------------------------------- configs ---

    def get_or_create_config(
        self, identity: EngineIdentity, settings: EngineSettings
    ) -> AnalysisConfig:
        """The config row for these settings; identical settings always share one row."""
        digest = config_hash(identity, settings)
        self._session.execute(
            pg_insert(AnalysisConfig)
            .values(
                engine_name=identity.name,
                engine_version=identity.version,
                nodes_per_position=settings.nodes_per_position,
                threads=settings.threads,
                hash_mb=settings.hash_mb,
                multipv=settings.multipv,
                config_hash=digest,
            )
            .on_conflict_do_nothing(index_elements=["config_hash"])
        )
        return self._session.scalars(
            select(AnalysisConfig).where(AnalysisConfig.config_hash == digest)
        ).one()

    # ------------------------------------------------------------------ games ---

    def game_to_analyse(self, game_id: int) -> GameToAnalyse | None:
        game = self._session.get(Game, game_id)
        if game is None:
            return None
        moves = self._session.scalars(
            select(GameMove.uci).where(GameMove.game_id == game_id).order_by(GameMove.ply)
        ).all()
        return GameToAnalyse(game.id, game.initial_fen, tuple(moves), game.moves_hash)

    def game_exists(self, game_id: int) -> bool:
        return self._session.get(Game, game_id) is not None

    def latest_games_needing_analysis(
        self,
        player_id: int,
        *,
        config_id: int,
        limit: int | None,
        among_latest: int | None = None,
    ) -> list[int]:
        """The player's newest games without a fresh, successful analysis by this config.

        With `among_latest=N`, only the player's N newest games are considered (the
        auto-analysis window, ADR-004); without it, the search goes back through history.
        `limit=None` returns all of them ("Analyse all", ADR-028).
        """
        fresh = (
            select(GameAnalysis.id)
            .where(
                GameAnalysis.game_id == Game.id,
                GameAnalysis.analysis_config_id == config_id,
                GameAnalysis.status == "succeeded",
                GameAnalysis.moves_hash == Game.moves_hash,
            )
            .exists()
        )
        newest_first = (Game.end_time.desc(), Game.id.desc())
        players_games = (
            select(Game.id)
            .join(GamePlayer, GamePlayer.game_id == Game.id)
            .where(GamePlayer.player_id == player_id)
        )
        query = players_games.where(~fresh)
        if among_latest is not None:
            window = players_games.order_by(*newest_first).limit(among_latest)
            query = query.where(Game.id.in_(window))
        return list(self._session.scalars(query.order_by(*newest_first).limit(limit)))

    def player_game_ids(self, player_id: int) -> list[int]:
        return list(
            self._session.scalars(
                select(GamePlayer.game_id).where(GamePlayer.player_id == player_id)
            )
        )

    def count_fresh_analysed(self, player_id: int) -> int:
        """The player's games with a fresh, successful analysis by any config (the same
        games the stats count as analysed)."""
        fresh = (
            select(GameAnalysis.id)
            .where(
                GameAnalysis.game_id == Game.id,
                GameAnalysis.status == "succeeded",
                GameAnalysis.moves_hash == Game.moves_hash,
            )
            .exists()
        )
        return self._session.scalar(
            select(func.count())
            .select_from(Game)
            .join(GamePlayer, GamePlayer.game_id == Game.id)
            .where(GamePlayer.player_id == player_id, fresh)
        )

    # -------------------------------------------------------------- analyses ---

    def get_analysis(self, game_id: int, config_id: int) -> GameAnalysis | None:
        return self._session.scalars(
            select(GameAnalysis).where(
                GameAnalysis.game_id == game_id, GameAnalysis.analysis_config_id == config_id
            )
        ).one_or_none()

    def mark_running(self, game_id: int, config_id: int, moves_hash: str) -> int:
        """Create the (game, config) analysis or restart it. Returns its id."""
        running = {
            "moves_hash": moves_hash,
            "status": "running",
            "started_at": func.now(),
            "finished_at": None,
            "error": None,
        }
        statement = pg_insert(GameAnalysis).values(
            game_id=game_id, analysis_config_id=config_id, **running
        )
        statement = statement.on_conflict_do_update(
            index_elements=["game_id", "analysis_config_id"],
            set_=running | {"updated_at": func.now()},
        ).returning(GameAnalysis.id)
        return self._session.execute(statement).scalar_one()

    def mark_failed(self, analysis_id: int, error: str) -> None:
        self._session.execute(
            update(GameAnalysis)
            .where(GameAnalysis.id == analysis_id)
            .values(status="failed", error=error, finished_at=func.now(), updated_at=func.now())
        )

    def save_evaluations(self, analysis_id: int, results: Sequence[EngineResult]) -> None:
        """Replace the analysis's evaluations (results[k] = position after k moves) and
        mark it succeeded. Classifications derived from the old evaluations are removed."""
        self._session.execute(
            delete(MoveClassification).where(MoveClassification.game_analysis_id == analysis_id)
        )
        self._session.execute(
            delete(PositionEvaluation).where(PositionEvaluation.game_analysis_id == analysis_id)
        )
        self._session.execute(
            insert(PositionEvaluation),
            [
                {
                    "game_analysis_id": analysis_id,
                    "ply": ply,
                    "pv_rank": 1,
                    "score_cp": result.score.cp,
                    "mate_in": result.score.mate,
                    "best_move_uci": result.best_move_uci,
                    "pv_uci": list(result.pv_uci) or None,
                    "depth_reached": result.depth,
                    "nodes_searched": result.nodes,
                }
                for ply, result in enumerate(results)
            ],
        )
        self._session.execute(
            update(GameAnalysis)
            .where(GameAnalysis.id == analysis_id)
            .values(status="succeeded", error=None, finished_at=func.now(), updated_at=func.now())
        )

    def load_evaluations(self, analysis_id: int) -> list[EngineResult]:
        rows = self._session.scalars(
            select(PositionEvaluation)
            .where(
                PositionEvaluation.game_analysis_id == analysis_id, PositionEvaluation.pv_rank == 1
            )
            .order_by(PositionEvaluation.ply)
        )
        return [
            EngineResult(
                score=Score(cp=row.score_cp, mate=row.mate_in),
                best_move_uci=row.best_move_uci,
                pv_uci=tuple(row.pv_uci or ()),
                depth=row.depth_reached,
                nodes=row.nodes_searched,
            )
            for row in rows
        ]

    # -------------------------------------------------------- classifications ---

    def has_classifications(self, analysis_id: int, classifier_version: int) -> bool:
        return self._session.scalar(
            select(
                select(MoveClassification.ply)
                .where(
                    MoveClassification.game_analysis_id == analysis_id,
                    MoveClassification.classifier_version == classifier_version,
                )
                .exists()
            )
        )

    def save_classifications(
        self, analysis_id: int, classifier_version: int, rows: Iterable[ClassificationRow]
    ) -> None:
        """Replace this classifier version's labels; other versions are kept."""
        self._session.execute(
            delete(MoveClassification).where(
                MoveClassification.game_analysis_id == analysis_id,
                MoveClassification.classifier_version == classifier_version,
            )
        )
        values = [
            {
                "game_analysis_id": analysis_id,
                "ply": ply,
                "classifier_version": classifier_version,
                "label": label,
                "eval_loss": loss,
            }
            for ply, label, loss in rows
        ]
        if values:  # a game with no moves has nothing to classify
            self._session.execute(insert(MoveClassification), values)
