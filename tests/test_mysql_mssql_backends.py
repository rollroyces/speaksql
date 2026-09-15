"""Tests for the MySQL and SQL Server backends."""

from __future__ import annotations

import pytest


def test_mysql_backend_class_loads():
    from speaksql.backends.mysql_backend import MySQLBackend

    assert MySQLBackend.name == "mysql"


def test_mssql_backend_class_loads():
    from speaksql.backends.mssql_backend import MSSQLBackend

    assert MSSQLBackend.name == "mssql"


def test_mysql_alias_resolves_through_factory(monkeypatch):
    """`mysql` should be a valid input to backend_for(); the alias isn't
    separately exposed but the dialect matches its own name."""
    from speaksql.backends import backend_for

    # Patch the constructor so no real connection is opened.
    captured = {}

    class FakeConn:
        def close(self):
            pass

    class FakeBackend:
        name = "mysql"

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr("speaksql.backends.mysql_backend.MySQLBackend", FakeBackend)
    backend_for("mysql", host="h", user="u", password="p", database="d")
    assert captured["host"] == "h"
    assert captured["database"] == "d"


def test_mssql_aliases_resolve_through_factory(monkeypatch):
    """`mssql` and `sqlserver` both resolve to MSSQLBackend."""
    from speaksql.backends import backend_for

    class FakeBackend:
        name = "mssql"

        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr("speaksql.backends.mssql_backend.MSSQLBackend", FakeBackend)
    for spelling in ("mssql", "sqlserver"):
        be = backend_for(spelling, server="h", user="u", password="p", database="d")
        assert be.name == "mssql"
        be.close()


def test_mysql_backend_requires_pymysql(monkeypatch):
    """If pymysql isn't installed, backend_for surfaces BackendError."""
    # Simulate missing module
    import builtins

    from speaksql.backends import backend_for
    from speaksql.exceptions import BackendError

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pymysql" or name.startswith("pymysql."):
            raise ImportError("simulated missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BackendError) as exc:
        backend_for("mysql")
    assert "pymysql" in str(exc.value).lower()


def test_mssql_backend_requires_pymssql(monkeypatch):
    import builtins

    from speaksql.backends import backend_for
    from speaksql.exceptions import BackendError

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pymssql" or name.startswith("pymssql."):
            raise ImportError("simulated missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BackendError) as exc:
        backend_for("mssql")
    assert "pymssql" in str(exc.value).lower()


def test_mysql_introspect_uses_information_schema(monkeypatch):
    """Smoke-test that the introspect query is well-formed SQLGlot SQL
    by exercising it through a fake cursor."""
    from speaksql.backends.mysql_backend import MySQLBackend

    # PyMySQL is configured to use DictCursor in the constructor, so the
    # fake cursor must return dicts too.
    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchall(self):
            sql = self.executed[-1][0]
            if "FROM information_schema.tables" in sql:
                return [{"TABLE_SCHEMA": "analytics", "TABLE_NAME": "users"}]
            if "FROM information_schema.columns" in sql:
                # Note: MySQL query aliases `is_pk` (column_key = 'PRI')
                return [
                    {
                        "COLUMN_NAME": "id",
                        "DATA_TYPE": "INT",
                        "IS_NULLABLE": "NO",
                        "is_pk": 1,
                    },
                    {
                        "COLUMN_NAME": "email",
                        "DATA_TYPE": "VARCHAR",
                        "IS_NULLABLE": "NO",
                        "is_pk": 0,
                    },
                ]
            return []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    be = MySQLBackend.__new__(MySQLBackend)
    be._conn = FakeConn()  # type: ignore[attr-defined]
    try:
        schema = be.introspect()
        assert len(schema.tables) == 1
        users = schema.tables[0]
        assert users.name == "users"
        assert users.schema == "analytics"
        assert len(users.columns) == 2
        assert users.columns[0].name == "id"
        assert users.columns[0].is_primary_key is True
    finally:
        be.close()


def test_mssql_introspect_uses_information_schema(monkeypatch):
    from speaksql.backends.mssql_backend import MSSQLBackend

    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchall(self):
            sql = self.executed[-1][0]
            if "FROM information_schema.tables" in sql:
                return [("dbo", "users")]
            if "FROM information_schema.columns" in sql:
                return [
                    ("id", "int", "NO", 1),
                    ("email", "varchar", "YES", 0),
                ]
            return []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    be = MSSQLBackend.__new__(MSSQLBackend)
    be._conn = FakeConn()  # type: ignore[attr-defined]
    try:
        schema = be.introspect()
        assert len(schema.tables) == 1
        users = schema.tables[0]
        assert users.name == "users"
        assert users.schema == "dbo"
        assert len(users.columns) == 2
        assert users.columns[0].name == "id"
        assert users.columns[0].is_primary_key is True
        assert users.columns[1].nullable is True
    finally:
        be.close()


def test_mysql_foreign_keys_uses_information_schema(monkeypatch):
    """FK detection should query information_schema.key_column_usage."""
    from speaksql.backends.mysql_backend import MySQLBackend

    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchall(self):
            return [
                {
                    "constraint_name": "fk_orders_users",
                    "from_table": "analytics.orders",
                    "from_column": "user_id",
                    "to_table": "analytics.users",
                    "to_column": "id",
                }
            ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    be = MySQLBackend.__new__(MySQLBackend)
    be._conn = FakeConn()  # type: ignore[attr-defined]
    try:
        fks = be.foreign_keys()
        assert len(fks) == 1
        assert fks[0].from_table == "analytics.orders"
        assert fks[0].to_column == "id"
        assert fks[0].constraint_name == "fk_orders_users"
    finally:
        be.close()


def test_mssql_foreign_keys_uses_sys_tables(monkeypatch):
    """MSSQL uses sys.foreign_keys instead of information_schema."""
    from speaksql.backends.mssql_backend import MSSQLBackend

    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchall(self):
            # MSSQL uses positional results, not dicts
            return [
                ("fk_orders_users", "dbo.orders", "user_id", "dbo.users", "id")
            ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    be = MSSQLBackend.__new__(MSSQLBackend)
    be._conn = FakeConn()  # type: ignore[attr-defined]
    try:
        fks = be.foreign_keys()
        assert len(fks) == 1
        assert fks[0].from_column == "user_id"
        assert fks[0].to_column == "id"
        assert fks[0].constraint_name == "fk_orders_users"
    finally:
        be.close()