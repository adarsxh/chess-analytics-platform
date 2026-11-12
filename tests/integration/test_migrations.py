"""Migration tests: the schema can be built from scratch, torn down, and matches the models."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from chess_analytics.storage.models import metadata
from chess_analytics.storage.schema import SCHEMAS

pytestmark = pytest.mark.integration


def our_schemas(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return set(inspect(conn).get_schema_names()) & set(SCHEMAS)
    finally:
        engine.dispose()


def test_migration_history_is_linear(alembic_config, empty_database_url: str) -> None:
    # Two migrations with the same parent (e.g. from two branches merged carelessly)
    # create multiple "heads"; `upgrade head` would then be ambiguous.
    script = ScriptDirectory.from_config(alembic_config(empty_database_url))
    assert len(script.get_heads()) == 1


def test_upgrade_downgrade_upgrade(alembic_config, empty_database_url: str) -> None:
    config = alembic_config(empty_database_url)

    command.upgrade(config, "head")
    assert our_schemas(empty_database_url) == set(SCHEMAS)

    command.downgrade(config, "base")
    assert our_schemas(empty_database_url) == set()

    # Upgrading again proves downgrade really cleaned up (no "already exists" errors).
    command.upgrade(config, "head")
    assert our_schemas(empty_database_url) == set(SCHEMAS)


def test_models_match_migrations(alembic_config, empty_database_url: str) -> None:
    # The equivalent of `alembic check`: after applying all migrations, autogenerate must
    # find no differences. Fails when someone changes a model but forgets the migration.
    command.upgrade(alembic_config(empty_database_url), "head")

    engine = create_engine(empty_database_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn,
                opts={
                    "include_schemas": True,
                    "include_name": lambda name, type_, _: type_ != "schema" or name in SCHEMAS,
                    "compare_type": True,
                },
            )
            diff = compare_metadata(context, metadata)
    finally:
        engine.dispose()

    assert diff == [], f"Models and migrations differ; create a migration: {diff}"
