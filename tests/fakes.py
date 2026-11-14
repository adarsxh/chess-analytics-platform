"""In-memory test doubles for the project's contracts."""

from dataclasses import dataclass, field

from chess_analytics.ingestion.base import (
    ArchiveFetchResult,
    ArchiveRef,
    PlayerNotFoundError,
    SourceGame,
    SourcePlayer,
)


@dataclass
class FakeGameSource:
    """A `GameSource` backed by dicts. Archive ETags are derived from their content,
    so changing an archive's games changes its ETag, just like the real platform."""

    platform: str = "chesscom"
    players: dict[str, SourcePlayer] = field(default_factory=dict)  # key: lower-case username
    archives: dict[str, list[ArchiveRef]] = field(default_factory=dict)
    games: dict[str, tuple[SourceGame, ...]] = field(default_factory=dict)  # key: archive url
    fetched: list[str] = field(default_factory=list)  # archive urls, in request order

    def get_player(self, username: str) -> SourcePlayer:
        try:
            return self.players[username.lower()]
        except KeyError:
            raise PlayerNotFoundError(username) from None

    def list_archives(self, username: str) -> list[ArchiveRef]:
        return sorted(self.archives.get(username.lower(), []), key=lambda a: a.month)

    def fetch_archive(self, archive: ArchiveRef, *, etag: str | None = None) -> ArchiveFetchResult:
        self.fetched.append(archive.url)
        games = self.games.get(archive.url, ())
        current = f'"{hash(tuple(g.platform_game_id for g in games))}"'
        if etag == current:
            return ArchiveFetchResult(archive=archive, http_status=304, etag=current)
        return ArchiveFetchResult(archive=archive, http_status=200, etag=current, games=games)
