"""Concrete backend adapters (live DB drivers).

This package holds DBAPI / SQLAlchemy drivers for each supported dialect.
Only the SQLite backend ships in the base install for development / tests.
Production backends install via extras:

    pip install speaksql[postgres]   # psycopg
    pip install speaksql[snowflake]  # snowflake-connector-python
    pip install speaksql[hana]       # hdbcli
    ...
"""

from __future__ import annotations

from typing import Any

from speaksql.introspect import Backend, SchemaList


class SQLiteBackend:
    """Local SQLite backend — useful for dev, tests, and demos."""

    name = "sqlite"

    def __init__(self, path: str = ":memory:") -> None:
        import sqlite3
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    def introspect(self) -> SchemaList:
        from speaksql.introspect import ColumnInfo, TableInfo
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = []
        for (name,) in cur.fetchall():
            cols = []
            for cid, cname, ctype, cnotnull, _dflt, _pk in self._conn.execute(
                f"PRAGMA table_info({name})"
            ).fetchall():
                cols.append(
                    ColumnInfo(
                        name=cname,
                        data_type=ctype,
                        nullable=not bool(cnotnull),
                        is_primary_key=bool(cid >= 0 and _pk),
                    )
                )
            tables.append(TableInfo(catalog=None, schema=None, name=name, columns=tuple(cols)))
        return SchemaList(tables=tuple(tables))

    def execute(self, sql: str) -> list[tuple]:
        cur = self._conn.execute(sql)
        try:
            return [tuple(r) for r in cur.fetchall()]
        finally:
            cur.close()

    def close(self) -> None:
        self._conn.close()


__all__ = ["Backend", "SQLiteBackend"]


def backend_for(dialect: str, **kwargs: Any) -> Backend:
    """Construct a backend for `dialect`. Imports driver lazily."""
    d = dialect.lower()
    if d == "sqlite":
        return SQLiteBackend(**kwargs)
    raise NotImplementedError(
        f"No bundled backend for dialect '{dialect}'. "
        "Install the matching driver extras (postgres, snowflake, hana, ...)."
    )