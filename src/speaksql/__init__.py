"""SpeakSQL — Speak once. Query any dialect.

A dialect-aware NL-to-SQL transpiler. Build a canonical ANSI query plan
and emit it correctly for Postgres, MySQL, SQL Server, Snowflake, BigQuery,
Databricks (Spark), DuckDB, and SAP HANA.
"""

from __future__ import annotations

from speaksql.core import SUPPORTED_DIALECTS, Dialect, Plan, emit, plan, transpile
from speaksql.exceptions import SpeakSQLError, TranspileError, UnsupportedDialectError
from speaksql.introspect import introspect

__version__ = "0.1.0"
__all__ = [
    "SUPPORTED_DIALECTS",
    "Dialect",
    "Plan",
    "SpeakSQLError",
    "TranspileError",
    "UnsupportedDialectError",
    "__version__",
    "emit",
    "introspect",
    "plan",
    "transpile",
]