"""Static checks on table definitions (no database needed)."""

from sqlalchemy import BigInteger, DateTime, String

from chess_analytics.storage.models import metadata
from chess_analytics.storage.schema import SCHEMAS


def test_every_table_lives_in_a_layer_schema() -> None:
    for table in metadata.tables.values():
        assert table.schema in SCHEMAS, f"{table.fullname} is not in a layer schema"


def test_foreign_key_types_match_referenced_columns() -> None:
    # An `integer` column pointing at a `bigint` key works until ids pass 2^31, then fails.
    foreign_keys = [fk for table in metadata.tables.values() for fk in table.foreign_keys]
    assert foreign_keys, "no foreign keys found: is the models package imported?"
    for table in metadata.tables.values():
        for fk in table.foreign_keys:
            local, remote = type(fk.parent.type), type(fk.column.type)
            assert local is remote, f"{table.fullname}.{fk.parent.name}: {local} → {remote}"


def test_surrogate_ids_are_bigint() -> None:
    for table in metadata.tables.values():
        if "id" in table.columns:
            assert isinstance(table.columns["id"].type, BigInteger), table.fullname


def test_timestamps_are_timezone_aware_and_no_varchar() -> None:
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone, f"{table.fullname}.{column.name} is naive"
            # String with a length = varchar(n); ADR-019 says text.
            if isinstance(column.type, String):
                assert column.type.length is None, f"{table.fullname}.{column.name} is varchar"
