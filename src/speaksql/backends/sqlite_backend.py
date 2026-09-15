"""SQLite backend — local, in-process, ships with the base install.

Useful for development, tests, demos, and any case where you want to
verify transpiled SQL against a real engine without standing up a server.
"""

from __future__ import annotations

import sqlite3

from speaksql.introspect import ColumnInfo, ForeignKeyInfo, SchemaList, TableInfo


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

    def foreign_keys(self) -> tuple[ForeignKeyInfo, ...]:
        """Read FKs from `PRAGMA foreign_key_list`.

        Returns a list per table. Each row has columns:
            id, seq, table, from, to, on_update, on_delete, match
        Note that PRAGMA only surfaces FKs on tables whose parent is
        already known to the database (i.e. created in this session
        or attached from another DB).
        """
        out: list[ForeignKeyInfo] = []
        for (table_name,) in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall():
            # PRAGMA doesn't support parameter binding; the table name
            # is sanitized because it comes from sqlite_master.
            rows = self._conn.execute(
                f"PRAGMA foreign_key_list({table_name})"
            ).fetchall()
            for row in rows:
                # row = (id, seq, ref_table, from, to, on_update, on_delete, match)
                _id, _seq, ref_table, from_col, to_col, *_rest = row
                out.append(
                    ForeignKeyInfo(
                        from_table=table_name,
                        from_column=from_col,
                        to_table=ref_table,
                        to_column=to_col,
                    )
                )
        return tuple(out)

    def close(self) -> None:
        self._conn.close()