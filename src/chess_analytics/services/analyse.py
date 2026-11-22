"""Analyse a game with the engine and classify its moves (DATA_MODEL.md §6, steps 5–6).

    ┌ tx 1: load game + moves, find/create the config, decide what is needed ┐
    │   fresh analysis + labels for this classifier → nothing to do          │
    │   fresh analysis, labels missing → re-classify stored evals (no engine)│
    │   otherwise → mark the analysis "running"                              │
    └────────────────────────────────────────────────────────────────────────┘
      engine: evaluate every position (≈ 40 s; NO transaction held open)
    ┌ tx 2: replace evaluations + classifications, mark "succeeded"           ┐
    └ (on failure: mark "failed" with the error, then re-raise)               ┘

Every step is idempotent: re-running a game produces the same rows, never duplicates.
"Fresh" means the analysed moves_hash still equals the game's (ADR-017); if the game's
moves change later, the next run re-analyses it in place.
"""

import time
from dataclasses import dataclass
from enum import StrEnum

import structlog
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.classification import moves as classifier
from chess_analytics.engine.base import Engine, EngineResult
from chess_analytics.engine.game import evaluate_game
from chess_analytics.storage.repositories.analysis import (
    AnalysisRepository,
    ClassificationRow,
    GameToAnalyse,
)
from chess_analytics.storage.repositories.queries import GameQueries

log = structlog.get_logger(__name__)


class GameNotFoundError(Exception):
    """No core game with this id."""


class Outcome(StrEnum):
    ANALYSED = "analysed"  # the engine ran
    RECLASSIFIED = "reclassified"  # stored evaluations re-labelled by a new classifier
    UP_TO_DATE = "up_to_date"  # nothing to do


@dataclass(frozen=True, slots=True)
class AnalysisSummary:
    game_id: int
    analysis_id: int
    config_id: int
    outcome: Outcome
    positions: int  # evaluated by the engine in this run (0 unless ANALYSED)
    seconds: float


def analyse_game(
    game_id: int, *, engine: Engine, session_factory: sessionmaker[Session]
) -> AnalysisSummary:
    started = time.monotonic()
    version = classifier.CLASSIFIER_VERSION

    def summary(analysis_id: int, config_id: int, outcome: Outcome, positions: int = 0):
        result = AnalysisSummary(
            game_id, analysis_id, config_id, outcome, positions, time.monotonic() - started
        )
        log.info("game_analysis_finished", **_fields(result))
        return result

    with session_factory.begin() as session:  # tx 1
        repo = AnalysisRepository(session)
        game = repo.game_to_analyse(game_id)
        if game is None:
            raise GameNotFoundError(f"game {game_id} not found")
        config_id = repo.get_or_create_config(engine.identity, engine.settings).id
        existing = repo.get_analysis(game_id, config_id)
        if (
            existing is not None
            and existing.status == "succeeded"
            and existing.moves_hash == game.moves_hash
        ):
            if repo.has_classifications(existing.id, version):
                return summary(existing.id, config_id, Outcome.UP_TO_DATE)
            rows = _classify(game, repo.load_evaluations(existing.id))
            repo.save_classifications(existing.id, version, rows)
            return summary(existing.id, config_id, Outcome.RECLASSIFIED)
        analysis_id = repo.mark_running(game_id, config_id, game.moves_hash)

    log.info(
        "game_analysis_started", game_id=game_id, config_id=config_id, plies=len(game.moves_uci)
    )
    try:
        results = [e.result for e in evaluate_game(engine, game.initial_fen, game.moves_uci)]
        rows = _classify(game, results)
    except Exception as exc:
        with session_factory.begin() as session:
            AnalysisRepository(session).mark_failed(analysis_id, f"{type(exc).__name__}: {exc}")
        raise

    with session_factory.begin() as session:  # tx 2
        repo = AnalysisRepository(session)
        repo.save_evaluations(analysis_id, results)
        repo.save_classifications(analysis_id, version, rows)
    return summary(analysis_id, config_id, Outcome.ANALYSED, positions=len(results))


def latest_games_needing_analysis(
    *,
    platform: str,
    username: str,
    limit: int,
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> list[int] | None:
    """Ids of the player's newest games without a fresh analysis by this engine's
    config, newest first. None if the player is unknown."""
    with session_factory.begin() as session:
        player = GameQueries(session).find_player(platform=platform, username=username)
        if player is None:
            return None
        repo = AnalysisRepository(session)
        config_id = repo.get_or_create_config(engine.identity, engine.settings).id
        return repo.latest_games_needing_analysis(player.id, config_id=config_id, limit=limit)


def _classify(game: GameToAnalyse, results: list[EngineResult]) -> list[ClassificationRow]:
    verdicts = classifier.classify_moves(game.initial_fen, game.moves_uci, results)
    return [(v.ply, v.label.value, v.win_loss) for v in verdicts]


def _fields(result: AnalysisSummary) -> dict[str, object]:
    return {
        "game_id": result.game_id,
        "analysis_id": result.analysis_id,
        "config_id": result.config_id,
        "outcome": result.outcome.value,
        "positions": result.positions,
        "seconds": round(result.seconds, 1),
    }
