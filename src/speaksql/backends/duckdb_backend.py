"""DuckDB backend — in-process analytical engine, also great for tests.

    pip install speaksql[duckdb]

DuckDB speaks a Postgres-flavoured dialect, so this is the cleanest
non-SQLite backend for local roundtrips. No server required.
"""

from __future__ import annotations

from speaksql.introspect import ColumnInfo, ForeignKeyInfo, SchemaList, TableInfo


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

    def foreign_keys(self) -> tuple[ForeignKeyInfo, ...]:
        """Read FKs from DuckDB's duckdb_constraints() system function.

        Returns a list of FKs whose constraint_type is 'FOREIGN KEY'.
        DuckDB exposes referenced_table and referenced_column_names
        directly — unlike SQLite's PRAGMA, no separate lookup needed.
        """
        rows = self._conn.execute(
            """
            SELECT schema_name, table_name, constraint_column_names,
                   referenced_table, referenced_column_names
            FROM duckdb_constraints()
            WHERE constraint_type = 'FOREIGN KEY'
              AND table_name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        out: list[ForeignKeyInfo] = []
        for schema, table, from_cols, ref_table, ref_cols in rows:
            # DuckDB stores these as Python lists via the duckdb driver.
            from_list = _as_list(from_cols)
            ref_list = _as_list(ref_cols)
            if not from_list or not ref_list:
                continue
            # Multi-column FKs: one entry per column pair
            for fc, rc in zip(from_list, ref_list):
                fq_table = f"{schema}.{table}" if schema else table
                fq_ref = f"{schema}.{ref_table}" if schema else ref_table
                out.append(
                    ForeignKeyInfo(
                        from_table=fq_table,
                        from_column=fc,
                        to_table=fq_ref,
                        to_column=rc,
                    )
                )
        return tuple(out)

    def close(self) -> None:
        self._conn.close()


def _as_list(v: object) -> list[str]:
    """Coerce duckdb array value to a Python list of strings."""
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, tuple):
        return [str(x) for x in v]
    return []