"""Backend tests (SQLite by default; others require their driver extras)."""

from __future__ import annotations

import pytest
from speaksql.backends import SQLiteBackend, backend_for


def test_sqlite_introspect_and_execute():
    b = SQLiteBackend(":memory:")
    try:
        b._conn.execute(
            "CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER)"
        )
        b._conn.execute("INSERT INTO t VALUES (1, 10), (2, 20)")
        schema = b.introspect()
        assert schema.find("t") is not None
        rows = b.execute("SELECT SUM(n) FROM t")
        assert rows == [(30,)]
    finally:
        b.close()


def test_backend_factory():
    b = backend_for("sqlite", path=":memory:")
    assert isinstance(b, SQLiteBackend)
    b.close()


def test_unsupported_backend_dialect():
    with pytest.raises(NotImplementedError):
        backend_for("postgres")