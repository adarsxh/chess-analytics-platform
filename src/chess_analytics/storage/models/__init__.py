"""SQLAlchemy table models, one module per Postgres schema.

Importing this package registers every table on `metadata`. Alembic and tests import
`metadata` from here (not from `storage.schema`) so they always see all tables.
"""

from chess_analytics.storage.models import analysis, core, ops, raw
from chess_analytics.storage.schema import metadata

__all__ = ["analysis", "core", "metadata", "ops", "raw"]
