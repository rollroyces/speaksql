"""PostgreSQL backend via psycopg 3.

    pip install speaksql[postgres]

Connection kwargs are passed straight through to `psycopg.connect`. Typical
usage:

    backend = PostgresBackend(
        host="localhost", port=5432, dbname="analytics",
        user="...", password="...",
    )

You can also pass a pre-built DSN string via `dsn=`.
"""

from __future__ import annotations

from typing import Any

from speaksql.introspect import ColumnInfo, SchemaList, TableInfo


class PostgresBackend:
    """PostgreSQL backend (psycopg 3 driver)."""

    name = "postgres"

    def __init__(self, **kwargs: Any) -> None:
        import psycopg

        if "dsn" in kwargs:
            self._conn = psycopg.connect(kwargs.pop("dsn"), autocommit=True)
        else:
            kwargs.setdefault("autocommit", True)
            self._conn = psycopg.connect(**kwargs)

    def introspect(self) -> SchemaList:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
                """
            )
            tables_meta = cur.fetchall()
            tables: list[TableInfo] = []
            for schema, name in tables_meta:
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable, column_name = ANY(
                        SELECT a.attname FROM pg_index i
                          JOIN pg_attribute a ON a.attrelid = i.indrelid
                         WHERE i.indrelid = (
                            SELECT c.oid FROM pg_class c
                              JOIN pg_namespace n ON n.oid = c.relnamespace
                             WHERE n.nspname = %s AND c.relname = %s
                         ) AND i.indisprimary
                    )
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, name, schema, name),
                )
                cols: list[ColumnInfo] = []
                for cname, ctype, nullable, is_pk in cur.fetchall():
                    cols.append(
                        ColumnInfo(
                            name=cname,
                            data_type=ctype,
                            nullable=(nullable == "YES"),
                            is_primary_key=bool(is_pk),
                        )
                    )
                tables.append(
                    TableInfo(catalog=None, schema=schema, name=name, columns=tuple(cols))
                )
        return SchemaList(tables=tuple(tables))

    def execute(self, sql: str) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                return []
            return [tuple(row) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()