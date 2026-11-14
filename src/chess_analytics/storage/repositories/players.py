"""Players and tracked players (DATA_MODEL.md §4.2, ADR-014).

Identity reminder: a player's stable identity is the platform's player *uuid*, but that
uuid only appears inside game data. A profile lookup gives us a username and, on
Chess.com, a numeric `player_id`. So a player first found by username is stored as a
*placeholder* (platform_uuid NULL); parsing their games later fills in the uuid.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chess_analytics.storage.models.core import Player, TrackedPlayer


class PlayerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_from_profile(
        self, *, platform: str, username: str, platform_player_id: int | None
    ) -> Player:
        """Find the player a profile belongs to, creating a placeholder if unknown.

        Lookup order, most reliable first:
        1. the numeric profile id (survives renames);
        2. the username, case-insensitively (preferring the most recently updated row);
        3. otherwise insert a placeholder.
        The stored username and numeric id are refreshed from the profile.
        """
        player = self._by_platform_player_id(platform, platform_player_id)
        if player is None:
            player = self._by_username(platform, username)
        if player is None:
            player = self._insert_placeholder(platform, username, platform_player_id)

        player.username = username
        if platform_player_id is not None:
            player.platform_player_id = platform_player_id
        self._session.flush()
        return player

    def resolve_from_game(self, *, platform: str, platform_uuid: str, username: str) -> Player:
        """Find or create the player behind one side of a game.

        1. By uuid: the stable identity. A changed username means a rename → updated.
        2. A placeholder (uuid unknown) with this username: this is the moment its uuid
           becomes known, so it is filled in (e.g. the synced player's own profile row).
        3. Otherwise a new player (typically an opponent).
        """
        player = self._session.scalar(
            select(Player).where(Player.platform == platform, Player.platform_uuid == platform_uuid)
        )
        if player is None:
            player = self._session.scalar(
                select(Player).where(
                    Player.platform == platform,
                    Player.platform_uuid.is_(None),
                    func.lower(Player.username) == username.lower(),
                )
            )
        if player is None:
            player = self._insert_known(platform, platform_uuid, username)

        player.platform_uuid = platform_uuid
        player.username = username
        self._session.flush()
        return player

    def track(self, player: Player) -> TrackedPlayer:
        """Mark a player as synced by us. Idempotent."""
        tracked = self._session.get(TrackedPlayer, player.id)
        if tracked is None:
            tracked = TrackedPlayer(player_id=player.id)
            self._session.add(tracked)
            self._session.flush()
        return tracked

    def mark_synced(self, player: Player) -> None:
        self.track(player).last_synced_at = func.now()
        self._session.flush()

    # ----------------------------------------------------------------- lookups ---

    def _by_platform_player_id(
        self, platform: str, platform_player_id: int | None
    ) -> Player | None:
        if platform_player_id is None:
            return None
        return self._session.scalar(
            select(Player)
            .where(Player.platform == platform, Player.platform_player_id == platform_player_id)
            .order_by(Player.id)
            .limit(1)
        )

    def _by_username(self, platform: str, username: str) -> Player | None:
        return self._session.scalar(
            select(Player)
            .where(Player.platform == platform, func.lower(Player.username) == username.lower())
            .order_by(Player.updated_at.desc(), Player.id.desc())
            .limit(1)
        )

    def _insert_known(self, platform: str, platform_uuid: str, username: str) -> Player:
        player = Player(platform=platform, platform_uuid=platform_uuid, username=username)
        try:
            # SAVEPOINT: a concurrent process may have inserted this uuid a moment ago.
            with self._session.begin_nested():
                self._session.add(player)
        except IntegrityError:
            existing = self._session.scalar(
                select(Player).where(
                    Player.platform == platform, Player.platform_uuid == platform_uuid
                )
            )
            if existing is None:
                raise
            return existing
        return player

    def _insert_placeholder(
        self, platform: str, username: str, platform_player_id: int | None
    ) -> Player:
        player = Player(platform=platform, username=username, platform_player_id=platform_player_id)
        try:
            # SAVEPOINT: if another process inserted the same placeholder a moment ago,
            # the unique index rejects ours and only this savepoint is rolled back.
            with self._session.begin_nested():
                self._session.add(player)
        except IntegrityError:
            existing = self._by_username(platform, username)
            if existing is None:  # the conflict was something else; don't hide it
                raise
            return existing
        return player
