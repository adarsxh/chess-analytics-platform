"""Read queries behind the API (ADR-008: operational reads go through repositories).

Each query returns plain dicts shaped for one use case, so route handlers contain no
SQL and the API's response models stay independent of the table layout.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from chess_analytics.storage.models.core import Game, GameMove, GamePlayer, Player, TrackedPlayer


class GameQueries:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ---------------------------------------------------------------- players ---

    def find_player(self, *, platform: str, username: str) -> Player | None:
        """Case-insensitive lookup. If a rename left two rows with this name, prefer the
        tracked one, then the most recently updated."""
        return self._session.scalar(
            select(Player)
            .outerjoin(TrackedPlayer, TrackedPlayer.player_id == Player.id)
            .where(Player.platform == platform, func.lower(Player.username) == username.lower())
            .order_by(TrackedPlayer.player_id.is_(None), Player.updated_at.desc())
            .limit(1)
        )

    def player_summary(self, player: Player) -> dict[str, Any]:
        outcomes = dict(
            self._session.execute(
                select(GamePlayer.outcome, func.count())
                .where(GamePlayer.player_id == player.id)
                .group_by(GamePlayer.outcome)
            ).all()
        )
        tracked = self._session.get(TrackedPlayer, player.id)
        return {
            "platform": player.platform,
            "username": player.username,
            "platform_uuid": player.platform_uuid,
            "tracked": tracked is not None,
            "last_synced_at": tracked.last_synced_at if tracked else None,
            "record": {
                "games": sum(outcomes.values()),
                "wins": outcomes.get("win", 0),
                "losses": outcomes.get("loss", 0),
                "draws": outcomes.get("draw", 0),
            },
        }

    # ------------------------------------------------------------------ games ---

    def list_player_games(
        self,
        player_id: int,
        *,
        limit: int,
        offset: int,
        time_class: str | None = None,
        outcome: str | None = None,
        rated: bool | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of a player's games, newest first, from that player's point of view.
        Returns (items, total matching)."""
        me, opponent = aliased(GamePlayer), aliased(GamePlayer)
        opponent_player = aliased(Player)

        filters = [me.player_id == player_id]
        if time_class is not None:
            filters.append(Game.time_class == time_class)
        if outcome is not None:
            filters.append(me.outcome == outcome)
        if rated is not None:
            filters.append(Game.rated.is_(rated))

        total = self._session.scalar(
            select(func.count()).select_from(me).join(Game, Game.id == me.game_id).where(*filters)
        )
        rows = self._session.execute(
            select(
                Game.id.label("game_id"),
                Game.platform_game_id,
                Game.url,
                Game.end_time,
                Game.time_class,
                Game.time_control,
                Game.rated,
                Game.ply_count,
                Game.source_eco.label("eco"),
                Game.source_opening_name.label("opening_name"),
                me.color,
                me.outcome,
                me.result_code,
                me.rating_after,
                me.source_accuracy.label("accuracy"),
                opponent_player.username.label("opponent_username"),
                opponent.rating_after.label("opponent_rating_after"),
            )
            .select_from(me)
            .join(Game, Game.id == me.game_id)
            .join(opponent, (opponent.game_id == me.game_id) & (opponent.color != me.color))
            .join(opponent_player, opponent_player.id == opponent.player_id)
            .where(*filters)
            .order_by(Game.end_time.desc(), Game.id.desc())
            .limit(limit)
            .offset(offset)
        ).mappings()
        return [dict(row) for row in rows], total or 0

    def game_detail(self, game_id: int, *, include_moves: bool = True) -> dict[str, Any] | None:
        game = self._session.get(Game, game_id)
        if game is None:
            return None
        sides = self._session.execute(
            select(
                GamePlayer.color,
                Player.username,
                GamePlayer.rating_after,
                GamePlayer.outcome,
                GamePlayer.result_code,
                GamePlayer.source_accuracy.label("accuracy"),
            )
            .join(Player, Player.id == GamePlayer.player_id)
            .where(GamePlayer.game_id == game_id)
        ).mappings()
        by_color = {side["color"]: dict(side) for side in sides}
        moves = []
        if include_moves:
            moves = [
                {
                    "ply": m.ply,
                    "san": m.san,
                    "uci": m.uci,
                    "fen_after": m.fen_after,
                    "clock_ms": m.clock_ms,
                    "time_spent_ms": m.time_spent_ms,
                }
                for m in self._session.scalars(
                    select(GameMove).where(GameMove.game_id == game_id).order_by(GameMove.ply)
                )
            ]
        return {
            "game_id": game.id,
            "platform": game.platform,
            "platform_game_id": game.platform_game_id,
            "url": game.url,
            "rules": game.rules,
            "time_class": game.time_class,
            "time_control": game.time_control,
            "rated": game.rated,
            "start_time": game.start_time,
            "end_time": game.end_time,
            "result": game.result,
            "termination": game.termination,
            "eco": game.source_eco,
            "opening_name": game.source_opening_name,
            "initial_fen": game.initial_fen,
            "final_fen": game.final_fen,
            "ply_count": game.ply_count,
            "white": by_color["white"],
            "black": by_color["black"],
            "moves": moves,
        }
