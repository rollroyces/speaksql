"""Smoke tests for the Postgres backend constructor.

Live execution tests would need a real Postgres server, which isn't
available in CI — so we only verify the driver loads, the class
implements the Backend protocol, and the connection error path
surfaces cleanly.
"""

from __future__ import annotations

from unittest.mock import patch as mock_patch

import psycopg
import pytest


def test_postgres_constructor_imports_driver():
    """Just importing psycopg shouldn't fail; the class should exist."""
    from speaksql.backends.postgres_backend import PostgresBackend

    assert hasattr(PostgresBackend, "introspect")
    assert hasattr(PostgresBackend, "execute")
    assert hasattr(PostgresBackend, "close")


def test_postgres_bad_host_raises_operational():

    from speaksql.backends import backend_for

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

    # Stub psycopg.connect so the constructor returns a FakeConn instead of
    # trying to dial out. We then call backend_for("postgres", ...) and
    # verify it doesn't raise BackendError when the driver IS installed.
    with mock_patch("psycopg.connect", return_value=FakeConn()):
        be = backend_for(
            "postgres",
            host="nonexistent.speaksql.test",
            dbname="x",
            user="x",
            password="x",
            connect_timeout=2,
        )
        assert be.name == "postgres"
        be.close()

    # Confirm a clean OperationalError surfaces when the driver really can't
    # connect (no monkeypatch this time):
    with pytest.raises(psycopg.OperationalError):
        backend_for(
            "postgres",
            host="nonexistent.speaksql.test",
            dbname="x",
            user="x",
            password="x",
            connect_timeout=2,
        )


def test_postgres_via_postgresql_alias():
    """`postgresql` should resolve identically to `postgres`."""

    from speaksql.backends import backend_for

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