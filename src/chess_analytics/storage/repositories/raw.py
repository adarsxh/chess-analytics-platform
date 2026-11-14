"""The `raw` layer: ingestion runs, the archive fetch log and raw game versions.

DATA_MODEL.md §4.1 and §6 ("Sync a player"), ADR-015. Rules this module implements:
- raw payloads are append-only: a changed game becomes a new row, never an update;
- storing the same version again is idempotent and only bumps `last_seen_at`;
- the ETag to send next time is the one from the latest successful fetch of that month.

The module takes plain values (not ingestion types), so storage does not depend on
any adapter.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.orm import Session

from chess_analytics.storage.models.raw import ArchiveFetch, IngestionRun, RawGame


def content_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the payload's canonical JSON: same content → same hash, whatever the
    key order or whitespace the source used."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoreResult:
    new_versions: int  # versions never seen before (inserted)
    seen_again: int  # versions already stored (last_seen_at bumped)


class RawRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------- runs ---

    def start_run(
        self,
        *,
        player_id: int,
        mode: str,
        month_from: date | None = None,
        month_to: date | None = None,
    ) -> IngestionRun:
        run = IngestionRun(
            player_id=player_id,
            mode=mode,
            month_from=month_from,
            month_to=month_to,
            status="running",
        )
        self._session.add(run)
        self._session.flush()
        return run

    def finish_run(self, run: IngestionRun, *, error: str | None = None) -> None:
        """Close a run: succeeded without an error, failed with one. Counters are
        updated on the run object by the caller as it goes."""
        run.status = "failed" if error else "succeeded"
        run.error = error
        run.finished_at = func.now()
        self._session.flush()

    # ---------------------------------------------------------- archive fetches ---

    def latest_etags(self, player_id: int) -> dict[date, str]:
        """ETag of the latest successful (200/304) fetch per archive month."""
        rows = self._session.execute(
            select(ArchiveFetch.archive_month, ArchiveFetch.etag)
            .where(
                ArchiveFetch.player_id == player_id,
                ArchiveFetch.http_status.in_((200, 304)),
            )
            # DISTINCT ON keeps the first row per month in ORDER BY order (= the newest).
            .ext(distinct_on(ArchiveFetch.archive_month))
            .order_by(
                ArchiveFetch.archive_month,
                ArchiveFetch.fetched_at.desc(),
                ArchiveFetch.id.desc(),
            )
        )
        return {month: etag for month, etag in rows if etag is not None}

    def record_fetch(
        self,
        run: IngestionRun,
        *,
        archive_month: date,
        url: str,
        http_status: int,
        etag: str | None,
        games_in_archive: int | None,
    ) -> ArchiveFetch:
        fetch = ArchiveFetch(
            ingestion_run_id=run.id,
            player_id=run.player_id,
            archive_month=archive_month,
            url=url,
            http_status=http_status,
            etag=etag,
            games_in_archive=games_in_archive,
        )
        self._session.add(fetch)
        self._session.flush()
        return fetch

    # ---------------------------------------------------------------- raw games ---

    def store_games(
        self, fetch: ArchiveFetch, *, platform: str, games: Mapping[str, Mapping[str, Any]]
    ) -> StoreResult:
        """Store one archive's games (platform_game_id → payload) in one statement.

        INSERT ... ON CONFLICT (platform, platform_game_id, content_hash)
            DO UPDATE SET last_seen_at = now()
        A new version is inserted; a known version only gets its last_seen_at bumped.
        Taking a mapping also means a game can't appear twice in one statement, which
        Postgres would reject ("ON CONFLICT DO UPDATE cannot affect row a second time").
        """
        if not games:
            fetch.new_versions_stored = 0
            return StoreResult(new_versions=0, seen_again=0)

        rows = [
            {
                "platform": platform,
                "platform_game_id": game_id,
                "content_hash": content_hash(payload),
                "payload": payload,
                "source_fetch_id": fetch.id,
            }
            for game_id, payload in games.items()
        ]
        statement = insert(RawGame).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["platform", "platform_game_id", "content_hash"],
            set_={"last_seen_at": func.now()},
        ).returning(
            # Postgres detail: xmax is 0 for a freshly inserted row and non-zero for a row
            # this statement updated, so it tells inserts and conflicts apart in one query.
            literal_column("xmax = 0").label("inserted")
        )
        inserted = sum(1 for (was_inserted,) in self._session.execute(statement) if was_inserted)

        fetch.new_versions_stored = inserted
        self._session.flush()
        return StoreResult(new_versions=inserted, seen_again=len(rows) - inserted)
