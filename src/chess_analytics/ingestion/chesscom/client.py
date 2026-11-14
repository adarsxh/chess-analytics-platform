"""Chess.com adapter: implements `GameSource` over the public data API.

API reference: https://www.chess.com/news/view/published-data-api
Facts verified against real responses in Phase 2 (DATA_MODEL.md, "Source facts"):
- every game has a `uuid` → used as platform_game_id;
- archive responses carry an ETag, and `If-None-Match` returns 304 when unchanged;
- requests made one at a time are not rate-limited; parallel requests may get 429.

Everything Chess.com-specific (URLs, JSON keys, status codes) stays in this module.
"""

import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any, Self

import httpx2
import structlog
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential_jitter,
)

from chess_analytics.config import Settings
from chess_analytics.ingestion.base import (
    ArchiveFetchResult,
    ArchiveRef,
    PlayerNotFoundError,
    SourceError,
    SourceGame,
    SourcePlayer,
    SourceUnavailableError,
)

PLATFORM = "chesscom"
MAX_ATTEMPTS = 4  # 1 request + 3 retries
MAX_RETRY_AFTER_SECONDS = 60.0  # never let a server header stall us longer than this

# Chess.com usernames are letters, digits, '_' and '-'. Validating also guarantees a
# username can never alter the request path (e.g. "../").
_USERNAME = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
_ARCHIVE_MONTH = re.compile(r"/games/(\d{4})/(\d{2})$")

log = structlog.get_logger(__name__)


# --------------------------------------------------------------- retry policy ---


def _is_retryable(response: httpx2.Response) -> bool:
    """429 = rate limited, 5xx = server trouble. Other errors will not fix themselves."""
    return response.status_code == 429 or response.status_code >= 500


_backoff = wait_exponential_jitter(initial=1, max=30)  # ~1s, 2s, 4s ... plus random jitter


def _wait(state: RetryCallState) -> float:
    """Honour the server's Retry-After (in seconds) if present, else back off exponentially."""
    outcome = state.outcome
    if outcome is not None and not outcome.failed:
        retry_after = outcome.result().headers.get("retry-after", "")
        if retry_after.isdigit():
            return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
    return _backoff(state)


def _log_retry(state: RetryCallState) -> None:
    outcome = state.outcome
    assert outcome is not None and state.next_action is not None
    log.warning(
        "chesscom_request_retry",
        url=str(state.args[0]) if state.args else None,
        attempt=state.attempt_number,
        wait_seconds=round(state.next_action.sleep, 2),
        cause=repr(outcome.exception()) if outcome.failed else outcome.result().status_code,
    )


def _last_outcome(state: RetryCallState) -> httpx2.Response:
    """When retries run out: return the last response, or re-raise the last exception."""
    assert state.outcome is not None
    return state.outcome.result()


# --------------------------------------------------------------------- client ---


class ChessComClient:
    """A `GameSource` for Chess.com. Use as a context manager so connections are closed."""

    platform = PLATFORM

    def __init__(self, http: httpx2.Client, *, sleep: Callable[[float], None] = time.sleep):
        # The HTTP client is injected: production passes a real one (see from_settings),
        # tests pass one with a MockTransport. `sleep` is injectable so retry tests are instant.
        self._http = http
        self._retrying = Retrying(
            retry=retry_if_exception_type(httpx2.TransportError) | retry_if_result(_is_retryable),
            wait=_wait,
            stop=stop_after_attempt(MAX_ATTEMPTS),
            sleep=sleep,
            before_sleep=_log_retry,
            retry_error_callback=_last_outcome,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Self:
        http = httpx2.Client(
            base_url=settings.chesscom_base_url,
            headers={"User-Agent": settings.chesscom_user_agent, "Accept": "application/json"},
            timeout=settings.chesscom_timeout_seconds,
            transport=transport,
        )
        return cls(http, sleep=sleep)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------ GameSource methods ---

    def get_player(self, username: str) -> SourcePlayer:
        url = f"/player/{_path_username(username)}"
        data = self._get_json(url, not_found=PlayerNotFoundError(username))
        try:
            return SourcePlayer(
                platform=PLATFORM,
                username=data["username"],
                platform_player_id=data.get("player_id"),
            )
        except (KeyError, TypeError) as exc:
            raise SourceError(f"unexpected profile response from {url}: {exc!r}") from exc

    def list_archives(self, username: str) -> list[ArchiveRef]:
        url = f"/player/{_path_username(username)}/games/archives"
        data = self._get_json(url, not_found=PlayerNotFoundError(username))
        try:
            refs = [ArchiveRef(month=_archive_month(u), url=u) for u in data["archives"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError(f"unexpected archives response from {url}: {exc!r}") from exc
        return sorted(refs, key=lambda ref: ref.month)

    def fetch_archive(self, archive: ArchiveRef, *, etag: str | None = None) -> ArchiveFetchResult:
        headers = {"If-None-Match": etag} if etag else {}
        response = self._get(archive.url, headers=headers)
        if response.status_code == 304:
            # Some servers omit the ETag on 304; the one we sent is still current.
            new_etag = response.headers.get("etag", etag)
            return ArchiveFetchResult(archive=archive, http_status=304, etag=new_etag)

        data = self._json(response, archive.url, not_found=None)
        try:
            games = tuple(g for g in map(_source_game, data["games"]) if g is not None)
        except (KeyError, TypeError, AttributeError) as exc:
            raise SourceError(f"unexpected archive response from {archive.url}: {exc!r}") from exc
        return ArchiveFetchResult(
            archive=archive,
            http_status=response.status_code,
            etag=response.headers.get("etag"),
            games=games,
        )

    # ---------------------------------------------------------------- HTTP ---

    def _get(self, url: str, headers: dict[str, str] | None = None) -> httpx2.Response:
        """GET with retries. Raises SourceUnavailableError if the API stays unhealthy."""
        try:
            response = self._retrying(self._http.get, url, headers=headers)
        except httpx2.TransportError as exc:  # network failure or timeout, every attempt
            raise SourceUnavailableError(f"GET {url} failed: {exc!r}") from exc
        if _is_retryable(response):
            raise SourceUnavailableError(
                f"GET {url} returned {response.status_code} after {MAX_ATTEMPTS} attempts"
            )
        return response

    def _get_json(self, url: str, *, not_found: SourceError) -> dict[str, Any]:
        return self._json(self._get(url), url, not_found=not_found)

    @staticmethod
    def _json(
        response: httpx2.Response, url: str, *, not_found: SourceError | None
    ) -> dict[str, Any]:
        if response.status_code == 404 and not_found is not None:
            raise not_found
        if response.status_code != 200:
            raise SourceError(f"GET {url} returned unexpected status {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceError(f"GET {url} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise SourceError(f"GET {url} returned JSON that is not an object")
        return data


# -------------------------------------------------------------------- mapping ---


def _path_username(username: str) -> str:
    if not _USERNAME.fullmatch(username):
        # No such account can exist, so this is "not found" rather than a server error.
        raise PlayerNotFoundError(username)
    return username.lower()  # Chess.com usernames are case-insensitive


def _archive_month(url: str) -> date:
    match = _ARCHIVE_MONTH.search(url)
    if match is None:
        raise ValueError(f"not an archive URL: {url}")
    return date(int(match[1]), int(match[2]), 1)


def _source_game(game: dict[str, Any]) -> SourceGame | None:
    game_id = game.get("uuid")
    if not game_id:
        # Never seen in real data (0 of 2,413 games). Skip loudly rather than invent an id.
        log.warning("chesscom_game_without_uuid", url=game.get("url"))
        return None
    return SourceGame(platform_game_id=game_id, payload=game)
