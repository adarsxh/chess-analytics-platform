"""The `core` layer: canonical games, their two sides and their moves.

DATA_MODEL.md §4.2 and §6, ADR-016. `core` is *current state*, rebuildable from `raw`:
- a game row is upserted by its natural key, so re-processing keeps its id (analyses in
  Phase 6 reference it) while every column is refreshed;
- its sides and moves are replaced as a whole, because they belong to the game;
- lineage (raw_game_id, parser_version) says exactly what produced the current row.

"Needs processing" is derived by query, never stored as a flag (DATA_MODEL §6).
"""

from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from chess_analytics.domain.game import ParsedGame
from chess_analytics.storage.models.core import Game, GameMove, GamePlayer, ProcessingFailure
from chess_analytics.storage.models.raw import RawGame
from chess_analytics.storage.repositories.players import PlayerRepository


class CoreRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------ reading raw ---

    def pending_raw_game_ids(self, *, parser_version: int, limit: int | None = None) -> list[int]:
        """Current raw versions that still need processing by this parser version.

        Current version of a game = its raw row seen most recently (DATA_MODEL §4.1).
        It needs processing when no core row was built from it by this parser version
        (or a newer one), and this parser version has not already rejected it.
        """
        current = (
            select(RawGame.id, RawGame.platform, RawGame.platform_game_id)
            .ext(distinct_on(RawGame.platform, RawGame.platform_game_id))
            .order_by(
                RawGame.platform,
                RawGame.platform_game_id,
                RawGame.last_seen_at.desc(),
                RawGame.id.desc(),
            )
            .cte("current_raw")
        )
        already_built = (
            select(Game.id)
            .where(
                Game.platform == current.c.platform,
                Game.platform_game_id == current.c.platform_game_id,
                Game.raw_game_id == current.c.id,
                Game.parser_version >= parser_version,
            )
            .exists()
        )
        already_rejected = (
            select(ProcessingFailure.raw_game_id)
            .where(
                ProcessingFailure.raw_game_id == current.c.id,
                ProcessingFailure.parser_version == parser_version,
            )
            .exists()
        )
        query = (
            select(current.c.id)
            .where(~already_built, ~already_rejected)
            .order_by(current.c.id)
            .limit(limit)
        )
        return list(self._session.scalars(query))

    def get_raw_game(self, raw_game_id: int) -> RawGame:
        return self._session.get_one(RawGame, raw_game_id)

    # ------------------------------------------------------------ writing core ---

    def save_game(self, parsed: ParsedGame, *, raw_game_id: int, parser_version: int) -> int:
        """Insert or refresh one game with its sides and moves. Returns the game id."""
        players = PlayerRepository(self._session)
        side_player_ids = {
            side.color: players.resolve_from_game(
                platform=parsed.platform, platform_uuid=side.platform_uuid, username=side.username
            ).id
            for side in (parsed.white, parsed.black)
        }

        game_id = self._upsert_game_row(parsed, raw_game_id, parser_version)

        # Children are replaced wholesale: simpler and safer than diffing move lists.
        self._session.execute(delete(GamePlayer).where(GamePlayer.game_id == game_id))
        self._session.execute(delete(GameMove).where(GameMove.game_id == game_id))
        self._session.execute(
            insert(GamePlayer),
            [
                {
                    "game_id": game_id,
                    "color": side.color,
                    "player_id": side_player_ids[side.color],
                    "rating_after": side.rating_after,
                    "result_code": side.result_code,
                    "outcome": side.outcome,
                    "source_accuracy": side.source_accuracy,
                }
                for side in (parsed.white, parsed.black)
            ],
        )
        if parsed.moves:
            self._session.execute(  # one batched INSERT for all plies
                insert(GameMove),
                [
                    {
                        "game_id": game_id,
                        "ply": move.ply,
                        "san": move.san,
                        "uci": move.uci,
                        "fen_after": move.fen_after,
                        "clock_ms": move.clock_ms,
                        "time_spent_ms": move.time_spent_ms,
                    }
                    for move in parsed.moves
                ],
            )
        return game_id

    def record_failure(self, *, raw_game_id: int, parser_version: int, error: str) -> None:
        """Remember that this parser version rejected this raw version (idempotent)."""
        statement = pg_insert(ProcessingFailure).values(
            raw_game_id=raw_game_id, parser_version=parser_version, error=error
        )
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=["raw_game_id", "parser_version"],
                set_={"error": statement.excluded.error, "failed_at": func.now()},
            )
        )

    # ---------------------------------------------------------------- helpers ---

    def _upsert_game_row(self, parsed: ParsedGame, raw_game_id: int, parser_version: int) -> int:
        values: dict[str, Any] = {
            "platform": parsed.platform,
            "platform_game_id": parsed.platform_game_id,
            "url": parsed.url,
            "raw_game_id": raw_game_id,
            "parser_version": parser_version,
            "rules": parsed.rules,
            "time_class": parsed.time_class,
            "time_control": parsed.time_control,
            "base_seconds": parsed.base_seconds,
            "increment_seconds": parsed.increment_seconds,
            "rated": parsed.rated,
            "start_time": parsed.start_time,
            "end_time": parsed.end_time,
            "result": parsed.result,
            "termination": parsed.termination,
            "initial_fen": parsed.initial_fen,
            "final_fen": parsed.final_fen,
            "ply_count": parsed.ply_count,
            "moves_hash": parsed.moves_hash,
            "source_eco": parsed.source_eco,
            "source_opening_name": parsed.source_opening_name,
        }
        statement = pg_insert(Game).values(**values)
        refreshed = {name: statement.excluded[name] for name in values}
        statement = statement.on_conflict_do_update(
            index_elements=["platform", "platform_game_id"],
            # Keep id and created_at; refresh everything else and stamp the processing time.
            set_=refreshed | {"processed_at": func.now(), "updated_at": func.now()},
        ).returning(Game.id)
        return self._session.execute(statement).scalar_one()
