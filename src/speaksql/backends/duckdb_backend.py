"""DuckDB backend — in-process analytical engine, also great for tests.

    pip install speaksql[duckdb]

DuckDB speaks a Postgres-flavoured dialect, so this is the cleanest
non-SQLite backend for local roundtrips. No server required.
"""

from __future__ import annotations

from speaksql.introspect import ColumnInfo, SchemaList, TableInfo


class DuckDBBackend:
    """DuckDB in-process backend."""

    name = "duckdb"

    def __init__(self, path: str = ":memory:", read_only: bool = False) -> None:
        import duckdb

        if read_only and path != ":memory:":
            self._conn = duckdb.connect(path, read_only=True)
        else:
            self._conn = duckdb.connect(path)

    def introspect(self) -> SchemaList:
        # DuckDB exposes information_schema like Postgres.
        rows = self._conn.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        ).fetchall()
        tables: list[TableInfo] = []
        for schema, name in rows:
            cols_rows = self._conn.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = ? AND table_name = ?
                ORDER BY ordinal_position
                """,
                [schema, name],
            ).fetchall()
            # DuckDB exposes PK info via duckdb_constraints(); we look for
            # PRIMARY KEY constraints and parse the column name list from
            # constraint_column_names.
            pk_rows = self._conn.execute(
                """
                SELECT constraint_column_names
                FROM duckdb_constraints()
                WHERE schema_name = ?
                  AND table_name = ?
                  AND constraint_type = 'PRIMARY KEY'
                """,
                [schema, name],
            ).fetchall()
            pk_set: set[str] = set()
            for (names,) in pk_rows:
                # DuckDB returns a LIST; duckdb python client gives a Python list
                # of strings. Fall back to stripping brackets if not.
                if isinstance(names, list):
                    pk_set.update(names)
                else:
                    pk_set.update(str(names).strip("[]").split(","))
            cols = tuple(
                ColumnInfo(
                    name=cname,
                    data_type=ctype,
                    nullable=(nullable == "YES"),
                    is_primary_key=(cname in pk_set),
                )
                for cname, ctype, nullable in cols_rows
            )
            tables.append(TableInfo(catalog=None, schema=schema, name=name, columns=cols))
        return SchemaList(tables=tuple(tables))

    def execute(self, sql: str) -> list[tuple]:
        result = self._conn.execute(sql)
        if result.description is None:
            return []
        return [tuple(row) for row in result.fetchall()]

    def close(self) -> None:
        self._conn.close()