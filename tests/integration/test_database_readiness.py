import pytest
from fastapi.testclient import TestClient

from chess_analytics.api.app import create_app

pytestmark = pytest.mark.integration


def test_readiness_ok_with_real_postgres(make_settings, test_database_url: str) -> None:
    app = create_app(make_settings(database_url=test_database_url))

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}
