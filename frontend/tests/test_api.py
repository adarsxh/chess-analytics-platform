"""The API client against a scripted HTTP transport (no backend needed)."""

from datetime import date

import httpx2
import pytest

from dashboard.api import ApiClient, ApiError, NotFound, filter_params


def client_for(handler) -> tuple[ApiClient, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    return ApiClient("http://api.test", transport=httpx2.MockTransport(record)), seen


def test_stats_requests_the_view_with_filters() -> None:
    api, seen = client_for(lambda r: httpx2.Response(200, json={"total": {}}))

    assert api.stats("me", "overview", filter_params("rapid", date(2026, 1, 1))) == {"total": {}}
    assert str(seen[0].url) == (
        "http://api.test/api/v1/players/me/stats/overview?time_class=rapid&since=2026-01-01"
    )


def test_filter_params_leave_out_unset_filters() -> None:
    assert filter_params() == {}
    assert filter_params(until=date(2026, 2, 3)) == {"until": "2026-02-03"}


def test_posts_for_sync_and_analysis() -> None:
    api, seen = client_for(lambda r: httpx2.Response(202, json={"job_id": 7}))
    assert api.request_sync("me")["job_id"] == 7
    assert api.request_analysis(42)["job_id"] == 7
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/api/v1/players/me/sync"),
        ("POST", "/api/v1/games/42/analysis"),
    ]


def test_analyse_all_request_progress_and_stop() -> None:
    api, seen = client_for(lambda r: httpx2.Response(200, json={"ok": r.method}))
    assert api.request_analyse_all("me") == {"ok": "POST"}
    assert api.analysis_progress("me") == {"ok": "GET"}
    assert api.stop_analyse_all("me") == {"ok": "DELETE"}
    assert {r.url.path for r in seen} == {"/api/v1/players/me/analysis"}


def test_404_is_not_found_with_the_api_detail() -> None:
    api, _ = client_for(lambda r: httpx2.Response(404, json={"detail": "player 'x' not found"}))
    with pytest.raises(NotFound, match="player 'x' not found"):
        api.player("x")


@pytest.mark.parametrize(
    "response", [httpx2.Response(500, text="boom"), httpx2.Response(422, json={"detail": "bad"})]
)
def test_other_errors_are_api_errors(response: httpx2.Response) -> None:
    api, _ = client_for(lambda r: response)
    with pytest.raises(ApiError) as caught:
        api.job(1)
    assert not isinstance(caught.value, NotFound)


def test_an_unreachable_api_is_an_api_error() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    api, _ = client_for(refuse)
    with pytest.raises(ApiError, match="cannot reach the API"):
        api.player("me")
