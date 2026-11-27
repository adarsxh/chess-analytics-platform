"""Job handlers: one function per job type, plus how their failures are classified.

A handler receives the job's payload and either returns (success) or raises:
- PermanentJobError: retrying cannot help (unknown player, malformed payload) → failed;
- RetryableJobError: may work later (Chess.com down) → requeued with backoff.
Any other exception is treated as retryable too: attempts are bounded by max_attempts,
and a genuine bug ends up `failed` with its message in `last_error`.
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol

import structlog
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.config import Settings
from chess_analytics.engine.base import Engine, EngineError
from chess_analytics.engine.stockfish import StockfishEngine
from chess_analytics.ingestion.base import GameSource, PlayerNotFoundError
from chess_analytics.ingestion.chesscom.client import ChessComClient
from chess_analytics.services.analyse import (
    ANALYSE_JOB_TYPE,
    ANALYSE_PLAYER_JOB_TYPE,
    GameNotFoundError,
    analyse_game,
    queue_all_for_analysis,
    queue_latest_for_analysis,
)
from chess_analytics.services.sync import SYNC_JOB_TYPE, SyncSummary, sync_player

log = structlog.get_logger(__name__)

Handler = Callable[[Mapping[str, Any]], None]
SourceFactory = Callable[[str], AbstractContextManager[GameSource]]


class PermanentJobError(Exception):
    """The job can never succeed as requested."""


class RetryableJobError(Exception):
    """The job failed for a reason that may go away (network, rate limit, outage)."""


# ------------------------------------------------------------------ engine ---


class ClosableEngine(Engine, Protocol):
    def close(self) -> None: ...


class EngineHolder:
    """The worker's engine: one process, started on first use and kept for later jobs
    (starting Stockfish and loading its network takes ~0.5 s). After an engine failure
    it is discarded, so the next job starts a fresh process."""

    def __init__(self, factory: Callable[[], ClosableEngine]) -> None:
        self._factory = factory
        self._engine: ClosableEngine | None = None

    def get(self) -> ClosableEngine:
        if self._engine is None:
            self._engine = self._factory()
        return self._engine

    def discard(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            try:
                engine.close()
            except EngineError:  # it was already broken; nothing more to clean up
                log.warning("engine_close_failed")

    close = discard


# ---------------------------------------------------------------- handlers ---


def handle_sync_player(
    payload: Mapping[str, Any],
    *,
    session_factory: sessionmaker[Session],
    source_factory: SourceFactory,
    on_synced: Callable[[SyncSummary], None] | None = None,
) -> None:
    platform, username = payload.get("platform"), payload.get("username")
    if not platform or not username:
        raise PermanentJobError(f"invalid sync_player payload: {dict(payload)}")

    with source_factory(platform) as source:
        try:
            summary = sync_player(username, source=source, session_factory=session_factory)
        except PlayerNotFoundError as exc:
            raise PermanentJobError(f"player not found: {username}") from exc

    if summary.status != "succeeded":
        # sync_player already recorded the failure on its ingestion run.
        raise RetryableJobError(summary.error or "sync failed")
    if on_synced is not None:
        on_synced(summary)


def handle_analyse_game(
    payload: Mapping[str, Any], *, session_factory: sessionmaker[Session], engines: EngineHolder
) -> None:
    game_id = payload.get("game_id")
    if type(game_id) is not int or game_id < 1:  # `type is`: True is an int, not a game id
        raise PermanentJobError(f"invalid analyse_game payload: {dict(payload)}")
    try:
        analyse_game(game_id, engine=engines.get(), session_factory=session_factory)
    except GameNotFoundError as exc:
        raise PermanentJobError(str(exc)) from exc
    except EngineError as exc:
        engines.discard()  # the process may be dead: the retry starts a new one
        raise RetryableJobError(f"engine failed: {exc}") from exc


def handle_analyse_player(
    payload: Mapping[str, Any], *, session_factory: sessionmaker[Session], engines: EngineHolder
) -> None:
    """ "Analyse all": queue a bulk analysis of every game this worker's engine config
    hasn't analysed. Needs the engine only for its identity (version and settings)."""
    platform, username = payload.get("platform"), payload.get("username")
    if not platform or not username:
        raise PermanentJobError(f"invalid analyse_player payload: {dict(payload)}")
    try:
        queued = queue_all_for_analysis(
            platform=platform, username=username, engine=engines.get(),
            session_factory=session_factory,
        )  # fmt: skip
    except EngineError as exc:
        engines.discard()
        raise RetryableJobError(f"engine failed: {exc}") from exc
    if queued is None:
        raise PermanentJobError(f"player not found: {username}")


def queue_auto_analysis(
    summary: SyncSummary,
    *,
    latest: int,
    session_factory: sessionmaker[Session],
    engines: EngineHolder,
) -> None:
    """After a sync: queue background analysis of the player's newest games. A broken
    engine doesn't fail the sync (its data is stored); it is logged and skipped."""
    try:
        queue_latest_for_analysis(
            summary.player_id, latest=latest, engine=engines.get(), session_factory=session_factory
        )
    except EngineError:
        engines.discard()
        log.warning("auto_analysis_skipped", player_id=summary.player_id, exc_info=True)


def build_handlers(
    settings: Settings, session_factory: sessionmaker[Session], engines: EngineHolder
) -> dict[str, Handler]:
    """Wire real dependencies into the handlers."""

    def source_factory(platform: str) -> AbstractContextManager[GameSource]:
        if platform != ChessComClient.platform:
            raise PermanentJobError(f"unsupported platform: {platform}")
        return ChessComClient.from_settings(settings)

    def on_synced(summary: SyncSummary) -> None:
        queue_auto_analysis(
            summary,
            latest=settings.auto_analyse_latest_n,
            session_factory=session_factory,
            engines=engines,
        )

    return {
        SYNC_JOB_TYPE: lambda payload: handle_sync_player(
            payload,
            session_factory=session_factory,
            source_factory=source_factory,
            on_synced=on_synced,
        ),
        ANALYSE_JOB_TYPE: lambda payload: handle_analyse_game(
            payload, session_factory=session_factory, engines=engines
        ),
        ANALYSE_PLAYER_JOB_TYPE: lambda payload: handle_analyse_player(
            payload, session_factory=session_factory, engines=engines
        ),
    }


def stockfish_holder(settings: Settings) -> EngineHolder:
    return EngineHolder(lambda: StockfishEngine.from_settings(settings))
