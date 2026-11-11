"""Shared pytest fixtures.

Test categories:
- unit tests (tests/unit): no external services; always run.
- integration tests (tests/integration, marked `integration`): need a real Postgres,
  given by CHESS_TEST_DATABASE_URL. Skipped when that variable is not set.
"""

import os
from collections.abc import Callable

import pytest

from chess_analytics.config import Settings

# Points at a closed port: connections are refused immediately, never hang.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://user:pass@127.0.0.1:1/none"


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Build Settings isolated from the developer's .env file."""

    def _make(**overrides: object) -> Settings:
        values: dict[str, object] = {"env": "test", "database_url": UNREACHABLE_DATABASE_URL}
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return _make


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("CHESS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CHESS_TEST_DATABASE_URL not set; start Postgres to run integration tests")
    return url
