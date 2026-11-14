"""Job handlers: one function per job type, plus how their failures are classified.

A handler receives the job's payload and either returns (success) or raises:
- PermanentJobError: retrying cannot help (unknown player, malformed payload) → failed;
- RetryableJobError: may work later (Chess.com down) → requeued with backoff.
Any other exception is treated as retryable too: attempts are bounded by max_attempts,
and a genuine bug ends up `failed` with its message in `last_error`.
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.config import Settings
from chess_analytics.ingestion.base import GameSource, PlayerNotFoundError
from chess_analytics.ingestion.chesscom.client import ChessComClient
from chess_analytics.services.sync import SYNC_JOB_TYPE, sync_player

Handler = Callable[[Mapping[str, Any]], None]
SourceFactory = Callable[[str], AbstractContextManager[GameSource]]


class PermanentJobError(Exception):
    """The job can never succeed as requested."""


class RetryableJobError(Exception):
    """The job failed for a reason that may go away (network, rate limit, outage)."""


def handle_sync_player(
    payload: Mapping[str, Any],
    *,
    session_factory: sessionmaker[Session],
    source_factory: SourceFactory,
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


def build_handlers(
    settings: Settings, session_factory: sessionmaker[Session]
) -> dict[str, Handler]:
    """Wire real dependencies into the handlers. `analyse_game` arrives in Phase 6."""

    def source_factory(platform: str) -> AbstractContextManager[GameSource]:
        if platform != ChessComClient.platform:
            raise PermanentJobError(f"unsupported platform: {platform}")
        return ChessComClient.from_settings(settings)

    return {
        SYNC_JOB_TYPE: lambda payload: handle_sync_player(
            payload, session_factory=session_factory, source_factory=source_factory
        ),
    }
