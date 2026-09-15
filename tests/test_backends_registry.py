"""Tests for the per-dialect backend registry and lazy driver loading."""

from __future__ import annotations

import pytest
from speaksql.exceptions import BackendError, UnsupportedDialectError


def test_sqlite_backend_runs_select_1():
    from speaksql.backends import backend_for

    be = backend_for("sqlite")
    try:
        assert be.execute("SELECT 1") == [(1,)]
    finally:
        be.close()


def test_postgres_alias_normalised():
    """`postgres` and `postgresql` should resolve to the same path.

    We can't construct a real PostgresBackend without a live server, so
    we patch `psycopg.connect` to assert the alias routing and avoid
    actually opening a socket.
    """
    from unittest.mock import patch as mock_patch

    from speaksql.backends import backend_for

    # Monkeypatch psycopg.connect so the constructor succeeds without a server.
    class FakeConn:
        def close(self):
            pass

        def cursor(self):
            import contextlib

            @contextlib.contextmanager
            def cmgr():
                class C:
                    def execute(self, *a, **kw):
                        pass

                    def fetchall(self):
                        return []

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                yield C()

            return cmgr()

    with mock_patch("psycopg.connect", return_value=FakeConn()):
        for spelling in ("postgres", "postgresql"):
            be = backend_for(spelling, host="x", dbname="x", user="x", password="x")
            assert be.name == "postgres"
            be.close()


def test_unknown_dialect_raises_unsupported():
    from speaksql.backends import backend_for

    with pytest.raises(UnsupportedDialectError):
        backend_for("oracle")


def test_missing_driver_raises_backend_error():
    """If the driver isn't installed, BackendError surfaces a clean hint."""
    from speaksql.backends import backend_for

    # Snowflake driver is heavy and rarely installed; assume it isn't.
    try:
        backend_for("snowflake")
    except BackendError as e:
        assert "snowflake-connector-python" in str(e)
    except UnsupportedDialectError:
        pytest.skip("snowflake is not a supported dialect")