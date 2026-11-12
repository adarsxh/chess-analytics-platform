"""Fixtures for tests that need a real Postgres.

Two kinds of database:
- `empty_database_url` (per test): a fresh, empty database, for migration tests that
  create and drop the schema themselves.
- `session` (per test, on a shared migrated database): the schema is built once per test
  run with `alembic upgrade head`; each test runs inside a transaction that is rolled back,
  so tests are isolated and nothing is ever committed.

Throwaway databases are dropped afterwards, so tests never touch development data.
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def temporary_database(server_url: str) -> Iterator[str]:
    """Create a uniquely named database on the server; drop it on exit."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    # CREATE/DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(server_url).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def make_alembic_config(database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


# ------------------------------------------------------------ migration tests ---


@pytest.fixture
def empty_database_url(test_database_url: str) -> Iterator[str]:
    with temporary_database(test_database_url) as url:
        yield url


@pytest.fixture
def alembic_config() -> Callable[[str], Config]:
    return make_alembic_config


# ------------------------------------------------------- data/constraint tests ---


@pytest.fixture(scope="session")
def migrated_engine(test_database_url: str) -> Iterator[Engine]:
    """One database per test run, built by the real migrations (not create_all)."""
    with temporary_database(test_database_url) as url:
        command.upgrade(make_alembic_config(url), "head")
        engine = create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def session(migrated_engine: Engine) -> Iterator[Session]:
    """An ORM session inside an outer transaction that is always rolled back.

    `create_savepoint` means code under test may call `session.begin_nested()` or even
    `session.commit()` without escaping the outer transaction.
    """
    with migrated_engine.connect() as connection:
        outer = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            try:
                yield db
            finally:
                db.close()
                outer.rollback()
