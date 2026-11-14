"""Database engine creation and connectivity checks.

The *engine* owns a pool of connections to Postgres. Create one per process, at startup,
and dispose it at shutdown. Creating an engine does not connect: the first connection
is opened lazily when something actually queries the database.
"""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from chess_analytics.config import Settings

# Fail fast instead of hanging when the database host is unreachable.
_CONNECT_TIMEOUT_SECONDS = 5


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        # Test each pooled connection before use, so a DB restart doesn't surface as
        # random "connection closed" errors in requests.
        pool_pre_ping=True,
        connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """A factory for ORM sessions. Use `with factory.begin() as session:` for one
    transaction that commits on success and rolls back on an exception."""
    return sessionmaker(engine)


def check_connection(engine: Engine) -> None:
    """Raise if the database cannot answer a trivial query."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
