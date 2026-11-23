"""The dashboard's only way to reach the backend: the HTTP API (ADR-005).

Each method maps to one endpoint and returns its JSON as plain dicts and lists. Errors
become two exceptions the pages can show: `NotFound` (404) and `ApiError` (anything else,
including the API being down).
"""

import os
from datetime import date
from typing import Any

import httpx2

DEFAULT_API_URL = "http://127.0.0.1:8000"


class ApiError(Exception):
    """The API failed or could not be reached."""


class NotFound(ApiError):
    """404: e.g. a player that was never synced, or a game not analysed yet."""


def filter_params(
    time_class: str | None = None, since: date | None = None, until: date | None = None
) -> dict[str, str]:
    """Query parameters for the stats endpoints; unset filters are left out."""
    params = {"time_class": time_class, "since": since, "until": until}
    return {key: str(value) for key, value in params.items() if value is not None}


class ApiClient:
    def __init__(self, base_url: str, *, transport: httpx2.BaseTransport | None = None) -> None:
        self._http = httpx2.Client(base_url=base_url, timeout=15, transport=transport)

    @classmethod
    def from_env(cls) -> "ApiClient":
        """CHESS_DASHBOARD_API_URL, e.g. http://api:8000 inside docker compose."""
        return cls(os.environ.get("CHESS_DASHBOARD_API_URL", DEFAULT_API_URL))

    # ---------------------------------------------------------------- players ---

    def player(self, username: str) -> dict[str, Any]:
        return self._get(f"/api/v1/players/{username}")

    def request_sync(self, username: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/players/{username}/sync")

    def job(self, job_id: int) -> dict[str, Any]:
        return self._get(f"/api/v1/jobs/{job_id}")

    def games(self, username: str, *, limit: int = 50, offset: int = 0,
              time_class: str | None = None) -> dict[str, Any]:  # fmt: skip
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if time_class:
            params["time_class"] = time_class
        return self._get(f"/api/v1/players/{username}/games", params)

    # ------------------------------------------------------------------ stats ---

    def stats(self, username: str, view: str, params: dict[str, Any]) -> Any:
        """One stats view: overview, rating-history, openings or accuracy."""
        return self._get(f"/api/v1/players/{username}/stats/{view}", params)

    # ------------------------------------------------------------------ games ---

    def game(self, game_id: int) -> dict[str, Any]:
        return self._get(f"/api/v1/games/{game_id}")

    def analysis(self, game_id: int) -> dict[str, Any]:
        return self._get(f"/api/v1/games/{game_id}/analysis")

    def request_analysis(self, game_id: int) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/games/{game_id}/analysis")

    # ---------------------------------------------------------------- helpers ---

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params)

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.request(method, path, params=params)
        except httpx2.TransportError as exc:
            raise ApiError(f"cannot reach the API at {self._http.base_url}: {exc}") from exc
        if response.status_code == 404:
            raise NotFound(_detail(response))
        if response.is_error:
            raise ApiError(f"{response.status_code}: {_detail(response)}")
        return response.json()


def _detail(response: httpx2.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text
