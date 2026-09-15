"""MySQL backend via PyMySQL (pure-Python MIT driver).

    pip install speaksql[mysql]

Connection kwargs are passed straight through to `pymysql.connect`.
Common ones: host, port, user, password, database, charset.

    backend = MySQLBackend(
        host="db.example.com", port=3306,
        user="readonly", password="...",
        database="analytics",
    )

PyMySQL has no real connection pool exposed at the DBAPI level, so we
open one connection per backend and let users wrap with a pool if needed.
"""

from __future__ import annotations

from typing import Any

from speaksql.introspect import ColumnInfo, ForeignKeyInfo, SchemaList, TableInfo


class MySQLBackend:
    """MySQL backend (PyMySQL driver)."""

    name = "mysql"

    def __init__(self, **kwargs: Any) -> None:
        import pymysql

        if "dsn" in kwargs:
            kwargs.pop("dsn")
        # PyMySQL has no autocommit on connect; default to True for read-mostly
        # schema introspection.
        kwargs.setdefault("autocommit", True)
        kwargs.setdefault("charset", "utf8mb4")
        # Cursor returns tuples by default — switch to dict for nicer FK reads.
        kwargs.setdefault("cursorclass", pymysql.cursors.DictCursor)
        self._conn = pymysql.connect(**kwargs)

    def introspect(self) -> SchemaList:
        """Read table + column metadata from information_schema."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN (
                  'mysql', 'information_schema', 'performance_schema',
                  'sys'
                )
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
                """
            )
            tables_meta = cur.fetchall()
            tables: list[TableInfo] = []
            for row in tables_meta:
                schema = row["TABLE_SCHEMA"]
                name = row["TABLE_NAME"]
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable,
                           (column_key = 'PRI') AS is_pk
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, name),
                )
                cols = tuple(
                    ColumnInfo(
                        name=r["COLUMN_NAME"],
                        data_type=r["DATA_TYPE"],
                        nullable=(r["IS_NULLABLE"] == "YES"),
                        is_primary_key=bool(r["is_pk"]),
                    )
                    for r in cur.fetchall()
                )
                tables.append(
                    TableInfo(catalog=None, schema=schema, name=name, columns=cols)
                )
        return SchemaList(tables=tuple(tables))

    def execute(self, sql: str) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                return []
            return [tuple(row.values()) for row in cur.fetchall()]

    def foreign_keys(self) -> tuple[ForeignKeyInfo, ...]:
        """Read FKs from information_schema.key_column_usage joined to
        referential_constraints (standard SQL)."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    kcu.constraint_name,
                    kcu.table_schema || '.' || kcu.table_name AS from_table,
                    kcu.column_name AS from_column,
                    ccu.table_schema || '.' || ccu.table_name AS to_table,
                    ccu.column_name AS to_column
                FROM information_schema.key_column_usage kcu
                JOIN information_schema.referential_constraints rc
                  ON kcu.constraint_name = rc.constraint_name
                  AND kcu.constraint_schema = rc.constraint_schema
                JOIN information_schema.key_column_usage ccu
                  ON rc.unique_constraint_name = ccu.constraint_name
                  AND rc.unique_constraint_schema = ccu.constraint_schema
                WHERE kcu.table_schema NOT IN (
                  'mysql', 'information_schema', 'performance_schema', 'sys'
                )
                ORDER BY kcu.table_name, kcu.ordinal_position
                """
            )
            rows = cur.fetchall()
        out: list[ForeignKeyInfo] = []
        for row in rows:
            out.append(
                ForeignKeyInfo(
                    from_table=row["from_table"],
                    from_column=row["from_column"],
                    to_table=row["to_table"],
                    to_column=row["to_column"],
                    constraint_name=row["constraint_name"],
                )
            )
        return tuple(out)

    def close(self) -> None:
        self._conn.close()