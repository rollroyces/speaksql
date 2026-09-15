"""Snowflake backend via snowflake-connector-python.

    pip install speaksql[snowflake]

Connection kwargs are forwarded to `snowflake.connector.connect`. Common
ones: user, password, account, warehouse, database, schema, role.

    backend = SnowflakeBackend(
        user="...", password="...", account="xy12345.us-east-1",
        warehouse="ANALYTICS_WH", database="ANALYTICS_DB", schema="PUBLIC",
    )
"""

from __future__ import annotations

from typing import Any

from speaksql.introspect import ColumnInfo, SchemaList, TableInfo


class SnowflakeBackend:
    """Snowflake backend (snowflake-connector-python driver)."""

    name = "snowflake"

    def __init__(self, **kwargs: Any) -> None:
        import snowflake.connector

        self._conn = snowflake.connector.connect(**kwargs)

    def introspect(self) -> SchemaList:
        cs = self._conn.cursor()
        try:
            cs.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('INFORMATION_SCHEMA')
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
                """
            )
            tables_meta = cs.fetchall()
            tables: list[TableInfo] = []
            for schema, name in tables_meta:
                cs.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, name),
                )
                cols = tuple(
                    ColumnInfo(
                        name=cname,
                        data_type=ctype,
                        nullable=(nullable == "YES"),
                        # PK lookup would need SHOW PRIMARY KEYS — skip for now
                        # and revisit if users ask. Snowflake's PK constraints
                        # are informational anyway (not enforced).
                        is_primary_key=False,
                    )
                    for cname, ctype, nullable in cs.fetchall()
                )
                tables.append(TableInfo(catalog=None, schema=schema, name=name, columns=cols))
        finally:
            cs.close()
        return SchemaList(tables=tuple(tables))

    def execute(self, sql: str) -> list[tuple]:
        cs = self._conn.cursor()
        try:
            cs.execute(sql)
            if cs.description is None:
                return []
            return [tuple(row) for row in cs.fetchall()]
        finally:
            cs.close()

    def close(self) -> None:
        self._conn.close()