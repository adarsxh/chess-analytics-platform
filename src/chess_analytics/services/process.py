"""Process raw games into the `core` layer (DATA_MODEL.md §6, step 4).

For each raw version that needs processing (derived by query, see CoreRepository):
    raw payload ──platform mapper──▶ ParsedGame ──CoreRepository──▶ core rows
in ONE transaction per game, so a game is either fully written or not at all.

- A rejected game (unsupported variant, inconsistent data) is recorded in
  `core.processing_failures` and skipped until PARSER_VERSION changes.
- An unexpected error (a bug, the database going away) stops the run loudly. Nothing
  is half-written, and re-running is safe because every step is idempotent.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.domain.game import GameRejectedError, ParsedGame
from chess_analytics.ingestion.chesscom import mapping as chesscom_mapping
from chess_analytics.storage.repositories.core import CoreRepository

# Version of the whole raw → core transformation (mapping + PGN parsing). Bump it when
# their output changes: every game becomes "needs processing" again, including games
# an older version rejected. Stored on each core.games row as lineage.
PARSER_VERSION = 1

# One mapper per platform; adding a platform means adding its mapper here.
MAPPERS: Mapping[str, Callable[[Mapping[str, Any]], ParsedGame]] = {
    chesscom_mapping.PLATFORM: chesscom_mapping.to_parsed_game,
}

PROGRESS_EVERY = 500

log = structlog.get_logger(__name__)


@dataclass
class ProcessSummary:
    pending: int = 0
    processed: int = 0
    rejected: int = 0


def process_pending(
    session_factory: sessionmaker[Session],
    *,
    parser_version: int = PARSER_VERSION,
    limit: int | None = None,
) -> ProcessSummary:
    with session_factory() as session:
        raw_ids = CoreRepository(session).pending_raw_game_ids(
            parser_version=parser_version, limit=limit
        )
    summary = ProcessSummary(pending=len(raw_ids))
    log.info("processing_started", pending=summary.pending, parser_version=parser_version)

    for done, raw_id in enumerate(raw_ids, start=1):
        with session_factory.begin() as session:  # one transaction per game
            if _process_one(CoreRepository(session), raw_id, parser_version):
                summary.processed += 1
            else:
                summary.rejected += 1
        if done % PROGRESS_EVERY == 0:
            log.info("processing_progress", done=done, of=summary.pending)

    log.info(
        "processing_finished",
        processed=summary.processed,
        rejected=summary.rejected,
        parser_version=parser_version,
    )
    return summary


def _process_one(core: CoreRepository, raw_id: int, parser_version: int) -> bool:
    """Returns True if the game was written to core, False if it was rejected."""
    raw = core.get_raw_game(raw_id)
    try:
        mapper = MAPPERS.get(raw.platform)
        if mapper is None:
            raise GameRejectedError(f"no mapper for platform {raw.platform!r}")
        parsed = mapper(raw.payload)
    except GameRejectedError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        core.record_failure(raw_game_id=raw_id, parser_version=parser_version, error=reason)
        log.info("game_rejected", raw_game_id=raw_id, reason=reason)
        return False

    core.save_game(parsed, raw_game_id=raw_id, parser_version=parser_version)
    return True
