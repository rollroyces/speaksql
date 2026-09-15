"""SQL Server backend via pymssql (Microsoft-sponsored pure-Python driver).

    pip install speaksql[mssql]

Connection kwargs are passed straight through to `pymssql.connect`.
Common ones: server, port, user, password, database, charset.

    backend = MSSQLBackend(
        server="sql.example.com",
        user="readonly", password="...",
        database="analytics",
    )

Note: pymssql uses `server=` instead of `host=`, unlike psycopg/pymysql.
"""

from __future__ import annotations

from typing import Any

from speaksql.introspect import ColumnInfo, ForeignKeyInfo, SchemaList, TableInfo


class MSSQLBackend:
    """SQL Server backend (pymssql driver)."""

    name = "mssql"

    def __init__(self, **kwargs: Any) -> None:
        import pymssql

        if "dsn" in kwargs:
            kwargs.pop("dsn")
        # pymssql auto-commits transactions, so no autocommit toggle.
        self._conn = pymssql.connect(**kwargs)

    def introspect(self) -> SchemaList:
        """Read table + column metadata from information_schema."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('sys', 'INFORMATION_SCHEMA',
                                           'guest', 'INFORMATION_SCHEMA')
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
                """
            )
            tables_meta = cur.fetchall()
            tables: list[TableInfo] = []
            for schema, name in tables_meta:
                cur.execute(
                    """
                    SELECT c.column_name, c.data_type, c.is_nullable,
                           CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END
                             AS is_pk
                    FROM information_schema.columns c
                    LEFT JOIN (
                      SELECT kcu.table_schema, kcu.table_name, kcu.column_name
                      FROM information_schema.key_column_usage kcu
                      JOIN information_schema.table_constraints tc
                        ON kcu.constraint_name = tc.constraint_name
                       AND kcu.table_schema = tc.table_schema
                      WHERE tc.constraint_type = 'PRIMARY KEY'
                    ) pk
                      ON c.table_schema = pk.table_schema
                     AND c.table_name = pk.table_name
                     AND c.column_name = pk.column_name
                    WHERE c.table_schema = %s AND c.table_name = %s
                    ORDER BY c.ordinal_position
                    """,
                    (schema, name),
                )
                cols = tuple(
                    ColumnInfo(
                        name=cname,
                        data_type=ctype,
                        nullable=(nullable == "YES"),
                        is_primary_key=bool(is_pk),
                    )
                    for cname, ctype, nullable, is_pk in cur.fetchall()
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
            return [tuple(row) for row in cur.fetchall()]

    def foreign_keys(self) -> tuple[ForeignKeyInfo, ...]:
        """Read FKs from sys.foreign_keys joined with sys.foreign_key_columns.

        This is the SQL Server-specific path — more reliable than the
        information_schema join because it gives column ordinal pairs
        directly without the multiple JOINs.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    fk.name AS constraint_name,
                    OBJECT_SCHEMA_NAME(fk.parent_object_id) + '.'
                      + OBJECT_NAME(fk.parent_object_id) AS from_table,
                    c1.name AS from_column,
                    OBJECT_SCHEMA_NAME(fk.referenced_object_id) + '.'
                      + OBJECT_NAME(fk.referenced_object_id) AS to_table,
                    c2.name AS to_column
                FROM sys.foreign_keys fk
                INNER JOIN sys.foreign_key_columns fkc
                  ON fk.object_id = fkc.constraint_object_id
                INNER JOIN sys.columns c1
                  ON fkc.parent_object_id = c1.object_id
                 AND fkc.parent_column_id = c1.column_id
                INNER JOIN sys.columns c2
                  ON fkc.referenced_object_id = c2.object_id
                 AND fkc.referenced_column_id = c2.column_id
                ORDER BY OBJECT_NAME(fk.parent_object_id), fkc.constraint_column_id
                """
            )
            rows = cur.fetchall()
        out: list[ForeignKeyInfo] = []
        for cname, from_table, from_col, to_table, to_col in rows:
            out.append(
                ForeignKeyInfo(
                    from_table=from_table,
                    from_column=from_col,
                    to_table=to_table,
                    to_column=to_col,
                    constraint_name=cname,
                )
            )
        return tuple(out)

    def close(self) -> None:
        self._conn.close()