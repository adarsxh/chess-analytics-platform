"""Sync a player: fetch their archives from a GameSource into the raw layer.

DATA_MODEL.md §6 "Sync a player", steps 1-4 (step 4, processing into `core`, is
services.process; run here after a successful fetch, decision P2-a).

Transaction boundaries, and why:
1. Start: resolve the player, mark them tracked, open an ingestion run → commit, so
   the run is visible as `running` while the sync is in progress.
2. One transaction per archive: its fetch-log row, its raw games and the run counters
   commit together. An archive's ETag is therefore only stored once its games are, and
   a crash halfway keeps every archive already finished (the next sync skips them).
3. Process: every pending raw game into `core`, one transaction per game. Games stored
   by a sync that then failed stay pending and are processed by the next run.
4. Finish: close the run (succeeded / failed) and stamp the player's last sync.
"""

from dataclasses import dataclass, field

import structlog
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.ingestion.base import ArchiveFetchResult, GameSource, SourceError
from chess_analytics.services.process import process_pending
from chess_analytics.storage.models.core import Player
from chess_analytics.storage.models.raw import IngestionRun
from chess_analytics.storage.repositories.players import PlayerRepository
from chess_analytics.storage.repositories.raw import RawRepository

log = structlog.get_logger(__name__)


@dataclass
class SyncSummary:
    run_id: int
    player_id: int
    username: str
    status: str = "running"
    archives_checked: int = 0
    archives_not_modified: int = 0
    games_seen: int = 0
    new_raw_versions: int = 0
    games_processed: int = 0
    games_rejected: int = 0
    error: str | None = field(default=None)


def sync_player(
    username: str,
    *,
    source: GameSource,
    session_factory: sessionmaker[Session],
    full_refresh: bool = False,
    process: bool = True,
) -> SyncSummary:
    """Fetch every archive of `username` and store new raw game versions.

    Raises PlayerNotFoundError (nothing is written). Any other SourceError marks the run
    failed and is reported in the returned summary. Unexpected errors (bugs) also mark
    the run failed, then propagate.
    """
    profile = source.get_player(username)
    mode = "full_refresh" if full_refresh else "incremental"

    with session_factory.begin() as session:
        players = PlayerRepository(session)
        player = players.resolve_from_profile(
            platform=source.platform,
            username=profile.username,
            platform_player_id=profile.platform_player_id,
        )
        players.track(player)
        raw = RawRepository(session)
        run = raw.start_run(player_id=player.id, mode=mode)
        etags = {} if full_refresh else raw.latest_etags(player.id)
        summary = SyncSummary(run_id=run.id, player_id=player.id, username=profile.username)

    bound = log.bind(run_id=summary.run_id, username=summary.username, mode=mode)
    bound.info("sync_started", known_etags=len(etags))

    try:
        for archive in source.list_archives(username):
            result = source.fetch_archive(archive, etag=etags.get(archive.month))
            with session_factory.begin() as session:
                new_versions = _store_archive(session, summary, result, source.platform)
            bound.info(
                "archive_synced",
                month=archive.month.isoformat(),
                http_status=result.http_status,
                games=len(result.games),
                new_versions=new_versions,
            )
        if process:
            processed = process_pending(session_factory)
            summary.games_processed = processed.processed
            summary.games_rejected = processed.rejected
    except SourceError as exc:
        _finish(session_factory, summary, error=f"{type(exc).__name__}: {exc}")
        bound.warning("sync_failed", error=summary.error)
        return summary
    except Exception as exc:
        _finish(session_factory, summary, error=f"unexpected {type(exc).__name__}: {exc}")
        bound.exception("sync_crashed")
        raise

    _finish(session_factory, summary)
    bound.info(
        "sync_finished",
        archives_checked=summary.archives_checked,
        archives_not_modified=summary.archives_not_modified,
        games_seen=summary.games_seen,
        new_raw_versions=summary.new_raw_versions,
        games_processed=summary.games_processed,
        games_rejected=summary.games_rejected,
    )
    return summary


def _store_archive(
    session: Session, summary: SyncSummary, result: ArchiveFetchResult, platform: str
) -> int:
    raw = RawRepository(session)
    run = session.get_one(IngestionRun, summary.run_id)
    fetch = raw.record_fetch(
        run,
        archive_month=result.archive.month,
        url=result.archive.url,
        http_status=result.http_status,
        etag=result.etag,
        games_in_archive=None if result.not_modified else len(result.games),
    )
    new_versions = 0
    if not result.not_modified:
        games = {game.platform_game_id: game.payload for game in result.games}
        new_versions = raw.store_games(fetch, platform=platform, games=games).new_versions

    summary.archives_checked += 1
    summary.archives_not_modified += result.not_modified
    summary.games_seen += len(result.games)
    summary.new_raw_versions += new_versions
    _copy_counters(summary, run)
    return new_versions


def _finish(
    session_factory: sessionmaker[Session], summary: SyncSummary, *, error: str | None = None
) -> None:
    with session_factory.begin() as session:
        run = session.get_one(IngestionRun, summary.run_id)
        _copy_counters(summary, run)
        RawRepository(session).finish_run(run, error=error)
        if error is None:
            PlayerRepository(session).mark_synced(session.get_one(Player, summary.player_id))
    summary.status = "failed" if error else "succeeded"
    summary.error = error


def _copy_counters(summary: SyncSummary, run: IngestionRun) -> None:
    run.archives_checked = summary.archives_checked
    run.archives_not_modified = summary.archives_not_modified
    run.games_seen = summary.games_seen
    run.new_raw_versions = summary.new_raw_versions
    run.games_processed = summary.games_processed
    run.games_failed = summary.games_rejected
