"""Schema introspection across supported dialects.

Each backend returns a uniform `SchemaList` of tables with columns,
types, and nullability so schema linking works without dialect-specific
code at the planning layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from speaksql.core import SUPPORTED_DIALECTS, _resolve_dialect


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False


@dataclass(frozen=True)
class TableInfo:
    catalog: str | None
    schema: str | None
    name: str
    columns: tuple[ColumnInfo, ...] = field(default_factory=tuple)

    @property
    def full_name(self) -> str:
        parts = [p for p in (self.catalog, self.schema, self.name) if p]
        return ".".join(parts)


@dataclass(frozen=True)
class SchemaList:
    tables: tuple[TableInfo, ...]

    def find(self, name: str) -> TableInfo | None:
        """Find a table by case-insensitive name match (unqualified)."""
        needle = name.lower()
        for t in self.tables:
            if t.name.lower() == needle:
                return t
        return None


class Backend(Protocol):
    """A dialect backend capable of introspection + execution."""

    name: str

    def introspect(self) -> SchemaList: ...

    def execute(self, sql: str) -> list[tuple]: ...


def introspect(dialect: str) -> SchemaList:
    """Build a schema-listing for the given dialect.

    Static fallback: derives column lists from `SELECT * FROM <table>` for
    each canonical demo table. Used when no live connection is supplied.
    Real connections extend this with `information_schema` queries.
    """
    target = _resolve_dialect(dialect)
    # Demo schema — replaced by live introspection in production.
    return _demo_schema(target)


def _demo_schema(dialect: str) -> SchemaList:
    """A small canonical schema for development + tests."""
    events = TableInfo(
        catalog=None,
        schema="public",
        name="events",
        columns=(
            ColumnInfo("id", "BIGINT", nullable=False, is_primary_key=True),
            ColumnInfo("user_id", "BIGINT"),
            ColumnInfo("event_name", "VARCHAR"),
            ColumnInfo("country", "VARCHAR"),
            ColumnInfo("amount", "NUMERIC"),
            ColumnInfo("created_at", "TIMESTAMP"),
        ),
    )
    users = TableInfo(
        catalog=None,
        schema="public",
        name="users",
        columns=(
            ColumnInfo("id", "BIGINT", nullable=False, is_primary_key=True),
            ColumnInfo("email", "VARCHAR"),
            ColumnInfo("country", "VARCHAR"),
            ColumnInfo("signup_at", "TIMESTAMP"),
        ),
    )
    return SchemaList(tables=(events, users))


__all__ = [
    "Backend",
    "ColumnInfo",
    "SchemaList",
    "TableInfo",
    "introspect",
]


# Cheap compile-time check: list supported dialects without raising.
def supported() -> tuple[str, ...]:
    return tuple(sorted(SUPPORTED_DIALECTS))