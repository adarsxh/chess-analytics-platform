from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from chess_analytics import __version__
from chess_analytics.api.app import create_app


@pytest.fixture
def client(make_settings) -> Iterator[TestClient]:
    # Database deliberately unreachable: unit tests run without Postgres.
    # `with` runs the app's lifespan (startup/shutdown), like a real server would.
    with TestClient(create_app(make_settings())) as test_client:
        yield test_client


def test_liveness_ok_even_without_database(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "env": "test"}


def test_readiness_reports_unavailable_database(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": "unavailable"}}


def test_openapi_schema_documents_health(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert {"/health", "/health/ready"} <= schema["paths"].keys()
    assert schema["info"]["version"] == __version__
