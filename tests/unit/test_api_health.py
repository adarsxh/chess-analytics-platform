from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from chess_analytics import __version__
from chess_analytics.api.app import create_app
from chess_analytics.config import Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(_env_file=None, env="test")
    # `with` runs the app's lifespan (startup/shutdown), like a real server would.
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "env": "test"}


def test_openapi_schema_documents_health(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/health" in schema["paths"]
    assert schema["info"]["version"] == __version__
