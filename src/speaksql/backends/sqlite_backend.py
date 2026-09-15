"""SQLite backend — local, in-process, ships with the base install.

Useful for development, tests, demos, and any case where you want to
verify transpiled SQL against a real engine without standing up a server.
"""

from __future__ import annotations

import sqlite3

from speaksql.introspect import ColumnInfo, SchemaList, TableInfo


class SQLiteBackend:
    """Local SQLite backend."""

    name = "sqlite"

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    def introspect(self) -> SchemaList:
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