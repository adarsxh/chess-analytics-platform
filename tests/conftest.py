"""Shared pytest fixtures.

Test categories:
- unit tests (tests/unit): no external services; always run.
- integration tests (tests/integration, marked `integration`): need a real Postgres,
  given by CHESS_TEST_DATABASE_URL. Skipped when that variable is not set.
- engine tests (tests/engine, marked `engine`): run a real Stockfish binary. Skipped when
  none is installed.
"""

import os
import shutil
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


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("CHESS_TEST_DATABASE_URL")
    if not url:
        # Locally, skipping is convenient. In CI (GitHub sets CI=true), a skip would turn
        # a misconfigured pipeline into a silently green build, so fail instead.
        if os.environ.get("CI"):
            pytest.fail("CHESS_TEST_DATABASE_URL must be set in CI")
        pytest.skip("CHESS_TEST_DATABASE_URL not set; start Postgres to run integration tests")
    return url


@pytest.fixture(scope="session")
def stockfish_path() -> str:
    """A Stockfish binary: CHESS_TEST_ENGINE_PATH, else `stockfish` on PATH."""
    path = os.environ.get("CHESS_TEST_ENGINE_PATH") or shutil.which("stockfish")
    if not path:
        if os.environ.get("CI"):  # same rule as the database: never a silent skip in CI
            pytest.fail("Stockfish must be installed in CI (scripts/install-stockfish.sh)")
        pytest.skip("Stockfish not found; `brew install stockfish` to run engine tests")
    return path
