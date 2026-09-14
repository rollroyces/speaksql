"""SpeakSQL exception hierarchy."""

from __future__ import annotations


class SpeakSQLError(Exception):
    """Base for all SpeakSQL errors."""


class UnsupportedDialectError(SpeakSQLError):
    """Raised when a dialect is requested but not supported."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        super().__init__(
            f"Dialect '{dialect}' is not supported. "
            f"Choose from: {sorted(SUPPORTED_DIALECTS_LIST)}"
        )


class TranspileError(SpeakSQLError):
    """Raised when a plan cannot be transpiled to a target dialect."""


class IntrospectionError(SpeakSQLError):
    """Raised when schema introspection fails."""


# Local import to avoid circular dependency with core
SUPPORTED_DIALECTS_LIST = frozenset(
    {
        "postgres",
        "mysql",
        "tsql",
        "snowflake",
        "bigquery",
        "spark",  # Databricks
        "duckdb",
        "sqlite",
        "hana",
    }
)