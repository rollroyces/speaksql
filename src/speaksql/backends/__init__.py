"""Concrete backend adapters (live DB drivers).

This package holds DBAPI / SQLAlchemy drivers for each supported dialect.
Only the SQLite backend ships in the base install for development / tests.
Production backends install via extras:

    pip install speaksql[postgres]   # psycopg
    pip install speaksql[duckdb]     # duckdb (in-process, also great for tests)
    pip install speaksql[snowflake]  # snowflake-connector-python
    pip install speaksql[bigquery]   # google-cloud-bigquery
    pip install speaksql[backends]   # all four at once

Each backend lives in its own module and is lazily imported — you only
pay for the driver you actually instantiate.
"""

from __future__ import annotations

from typing import Any

from speaksql.introspect import Backend

from .sqlite_backend import SQLiteBackend

__all__ = ["Backend", "SQLiteBackend", "backend_for"]


def backend_for(dialect: str, **kwargs: Any) -> Backend:
    """Construct a backend for `dialect`. Imports driver lazily.

    Raises:
        UnsupportedDialectError: dialect string not recognized.
        BackendError: driver is not installed for a recognized dialect.
    """
    from speaksql.exceptions import BackendError, UnsupportedDialectError

    d = dialect.lower().strip()

    # SQLite ships in the base install.
    if d == "sqlite":
        return SQLiteBackend(**kwargs)

    # Lazily import each driver so missing extras surface a clean BackendError.
    if d in ("postgres", "postgresql"):
        try:
            from .postgres_backend import PostgresBackend
        except ImportError as e:
            raise BackendError(
                "postgres backend requires 'psycopg[binary]': "
                "pip install speaksql[postgres]"
            ) from e
        # Fail fast if the driver itself is missing, not deep inside __init__.
        try:
            import psycopg  # noqa: F401
        except ImportError as e:
            raise BackendError(
                "postgres backend requires 'psycopg[binary]': "
                "pip install speaksql[postgres]"
            ) from e
        return PostgresBackend(**kwargs)

    if d == "duckdb":
        try:
            from .duckdb_backend import DuckDBBackend
        except ImportError as e:
            raise BackendError(
                "duckdb backend requires 'duckdb': pip install speaksql[duckdb]"
            ) from e
        try:
            import duckdb  # noqa: F401
        except ImportError as e:
            raise BackendError(
                "duckdb backend requires 'duckdb': pip install speaksql[duckdb]"
            ) from e
        return DuckDBBackend(**kwargs)

    if d == "snowflake":
        try:
            from .snowflake_backend import SnowflakeBackend
        except ImportError as e:
            raise BackendError(
                "snowflake backend requires 'snowflake-connector-python': "
                "pip install speaksql[snowflake]"
            ) from e
        try:
            import snowflake.connector  # noqa: F401
        except ImportError as e:
            raise BackendError(
                "snowflake backend requires 'snowflake-connector-python': "
                "pip install speaksql[snowflake]"
            ) from e
        return SnowflakeBackend(**kwargs)

    if d == "bigquery":
        try:
            from .bigquery_backend import BigQueryBackend
        except ImportError as e:
            raise BackendError(
                "bigquery backend requires 'google-cloud-bigquery': "
                "pip install speaksql[bigquery]"
            ) from e
        try:
            from google.cloud import bigquery  # noqa: F401
        except ImportError as e:
            raise BackendError(
                "bigquery backend requires 'google-cloud-bigquery': "
                "pip install speaksql[bigquery]"
            ) from e
        return BigQueryBackend(**kwargs)

    raise UnsupportedDialectError(dialect)