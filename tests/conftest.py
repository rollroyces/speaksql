"""Pytest fixtures + helpers for SpeakSQL."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# Make src/ importable without an install
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


_SERVICE_DEPS = ("fastapi", "starlette", "httpx")


def _service_extras_available() -> bool:
    return all(importlib.util.find_spec(name) for name in _SERVICE_DEPS)


# Skip the entire `service` module if optional deps are absent so the
# default test run is robust on either install profile.
if not _service_extras_available():
    pytest.skip(
        "fastapi/starlette/httpx not installed; skipping service tests",
        allow_module_level=True,
    )


# Per-backend deps — used to gate test modules that need a live driver.
_BACKEND_DEPS: dict[str, tuple[str, ...]] = {
    "duckdb": ("duckdb",),
    "postgres": ("psycopg",),
    "snowflake": ("snowflake",),
    "bigquery": ("google",),  # top-level namespace; google.cloud.bigquery
}


def backend_driver_available(dialect: str) -> bool:
    deps = _BACKEND_DEPS.get(dialect, ())
    return all(importlib.util.find_spec(d) for d in deps)


@pytest.fixture(scope="session")
def service_extras_available() -> bool:
    return _service_extras_available()


@pytest.fixture(scope="session")
def duckdb_backend():
    """A DuckDB backend backed by an in-memory database with seeded events."""
    if not backend_driver_available("duckdb"):
        pytest.skip("duckdb not installed; pip install speaksql[duckdb]")

    from speaksql.backends import backend_for

    be = backend_for("duckdb")
    be.execute(
        "CREATE TABLE events ("
        "  id INTEGER PRIMARY KEY, "
        "  country TEXT NOT NULL, "
        "  amount DOUBLE, "
        "  created_at TIMESTAMP"
        ")"
    )
    be.execute(
        "INSERT INTO events VALUES "
        "(1, 'JP', 100.0, '2026-01-15'),"
        "(2, 'JP', 200.0, '2026-02-20'),"
        "(3, 'US', 150.0, '2026-01-10'),"
        "(4, 'US', 250.0, '2026-02-25'),"
        "(5, 'CN', 300.0, '2026-03-05')"
    )
    yield be
    be.close()


@pytest.fixture(scope="session")
def sqlite_backend():
    """A SQLite backend backed by an in-memory database with seeded events."""
    from speaksql.backends import backend_for

    be = backend_for("sqlite")
    be.execute(
        "CREATE TABLE events ("
        "  id INTEGER PRIMARY KEY, "
        "  country TEXT NOT NULL, "
        "  amount REAL, "
        "  created_at TEXT"
        ")"
    )
    be.execute(
        "INSERT INTO events VALUES "
        "(1, 'JP', 100.0, '2026-01-15'),"
        "(2, 'JP', 200.0, '2026-02-20'),"
        "(3, 'US', 150.0, '2026-01-10'),"
        "(4, 'US', 250.0, '2026-02-25'),"
        "(5, 'CN', 300.0, '2026-03-05')"
    )
    yield be
    be.close()