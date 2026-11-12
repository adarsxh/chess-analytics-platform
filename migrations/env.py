"""Alembic environment: how migrations connect to the database.

Alembic runs this file for every command. It decides
- which database to connect to (our settings, or an override passed by tests), and
- which metadata to compare against for `--autogenerate` and `alembic check`.
"""

from alembic import context
from sqlalchemy import create_engine

from chess_analytics.config import get_settings
from chess_analytics.logging_setup import configure_logging
from chess_analytics.storage.schema import SCHEMAS, metadata

config = context.config


def _database_url() -> str:
    # Tests pass an explicit URL (a throwaway database); otherwise use app settings.
    override = config.attributes.get("database_url")
    if override:
        return override
    return get_settings().database_url.get_secret_value()


def _include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    # Only look at our own schemas. Without this, autogenerate would see other schemas
    # in the database (e.g. `public`, where alembic_version lives) and propose dropping them.
    if type_ == "schema":
        return name in SCHEMAS
    return True


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=metadata,
        include_schemas=True,
        include_name=_include_name,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of executing it."""
    _configure(url=_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    try:
        with engine.connect() as connection:
            _configure(connection=connection)
            # One transaction for the whole upgrade: Postgres supports transactional DDL,
            # so a failing migration rolls back completely instead of leaving a half-built schema.
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


def _setup_logging() -> None:
    # CLI runs use the application's logging (JSON in containers, console locally).
    # Tests call Alembic programmatically with an explicit URL and own their logging.
    if "database_url" in config.attributes:
        return
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


_setup_logging()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
