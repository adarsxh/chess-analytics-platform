"""Fixtures for tests that need a real Postgres.

Each test that changes the schema gets its own throwaway database, created and dropped
around the test. Tests can therefore never touch your development data, and they don't
depend on each other's leftovers.
"""

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def empty_database_url(test_database_url: str) -> Iterator[str]:
    """Create a fresh, empty database; drop it after the test."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    # CREATE/DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    admin = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(test_database_url).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def alembic_config() -> Callable[[str], Config]:
    """Build an Alembic config for the repo's migrations, pointed at a given database."""

    def _make(database_url: str) -> Config:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.attributes["database_url"] = database_url
        return config

    return _make
