"""SpeakSQL core: canonical plan + dialect emission.

The flow:
1. Build a canonical ANSI SQLGlot AST (the *plan*).
2. Use SQLGlot to transpile to each target dialect.
3. Apply SpeakSQL's override map for known SQLGlot gaps
   (dialect-specific functions, datatypes, syntactic idioms).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import sqlglot
from sqlglot import exp

from speaksql.exceptions import (
    SUPPORTED_DIALECTS_LIST,
    TranspileError,
    UnsupportedDialectError,
)

Dialect = Literal[
    "postgres", "mysql", "tsql", "snowflake", "bigquery", "spark", "duckdb", "hana"
]

# Each entry: name -> sqlglot dialect identifier.
# Spark is the SQLGlot dialect name; we label it "spark" / "databricks" in our API.
_DIALECT_ALIASES: dict[str, str] = {
    "postgres": "postgres",
    "mysql": "mysql",
    "tsql": "tsql",
    "sqlserver": "tsql",
    "mssql": "tsql",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "spark": "spark",
    "databricks": "spark",
    "duckdb": "duckdb",
    "sqlite": "sqlite",
    # HANA: now routes to a real vendor dialect registered in
    # speaksql.dialects.hana. We import lazily to avoid forcing the
    # HANA module on every import (it monkey-patches SQLGlot).
    "hana": "hana",
    "saphana": "hana",
}

SUPPORTED_DIALECTS = frozenset(SUPPORTED_DIALECTS_LIST)


def _resolve_dialect(dialect: str) -> str:
    d = dialect.lower().strip()
    if d in _DIALECT_ALIASES:
        target = _DIALECT_ALIASES[d]
        # Lazily register the HANA dialect — its module monkey-patches
        # SQLGlot, so we don't want to import it on every call.
        if target == "hana":
            from speaksql.dialects.hana import _register as _register_hana
            _register_hana()
        return target
    if d in SUPPORTED_DIALECTS_LIST:
        return d
    raise UnsupportedDialectError(d)


@dataclass
class Plan:
    """A canonical ANSI SQL plan expressed as a SQLGlot expression.

    Carries enough metadata for emitting to any target dialect. The
    `ast` is the source of truth — emit() re-renders it per dialect.
    """

    ast: exp.Expression
    source_dialect: str = "",
    description: str | None = None

    def sql(self, target: str) -> str:
        """Render the plan as SQL for the given target dialect."""
        return emit(self, target)

    def __repr__(self) -> str:  # pragma: no cover
        head = self.ast.sql(dialect="ansi")[:60]
        return f"Plan(<{head!r}>, dialects={sorted(SUPPORTED_DIALECTS_LIST)})"


def plan(
    canonical_sql: str,
    *,
    description: str | None = None,
    source_dialect: str = "",  # SQLGlot's canonical/empty dialect = ANSI-flavoured
) -> Plan:
    """Build a canonical Plan from an ANSI SQL string.

    The input should be ANSI-compatible SQL. It is parsed and re-canonicalized
    so downstream emitters always work from a clean AST.
    """
    try:
        ast = sqlglot.parse_one(
            canonical_sql,
            dialect=source_dialect or None,
        )
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as e:
        raise TranspileError(f"Failed to parse canonical SQL: {e}") from e
    if ast is None:
        raise TranspileError("Empty canonical SQL input")
    return Plan(ast=ast, source_dialect=source_dialect, description=description)


def emit(plan_obj: Plan, target: str) -> str:
    """Render a Plan as target-dialect SQL.

    SQLGlot handles the heavy lifting; we apply an override map for
    vendor-specific functions that SQLGlot's transpilation gets wrong
    or leaves as-is (e.g. HANA's `add_months`, SQL Server's `DATEDIFF`).
    """
    target_dialect = _resolve_dialect(target)
    try:
        sql = plan_obj.ast.sql(dialect=target_dialect, pretty=True)
    except Exception as e:  # SQLGlot raises broad exceptions on bad emit
        raise TranspileError(
            f"Failed to emit SQL for dialect '{target_dialect}': {e}"
        ) from e
    sql = _apply_overrides(sql, target_dialect, plan_obj.ast)
    return _post_format(sql, target_dialect)


def transpile(
    canonical_sql: str,
    targets: str | Iterable[str],
    *,
    description: str | None = None,
) -> dict[str, str]:
    """Convenience: canonical SQL in, dict of {dialect: sql} out."""
    p = plan(canonical_sql, description=description)
    if isinstance(targets, str):
        targets = [targets]
    return {t: emit(p, t) for t in targets}


def _apply_overrides(sql: str, dialect: str, ast: exp.Expression) -> str:
    """Targeted textual overrides applied to emitted SQL.

    Delegates to `vendor_overrides.apply_overrides()`, which walks the
    registered per-dialect override functions. Overrides should be
    narrow, idempotent textual fixes for things SQLGlot's emitter gets
    wrong (e.g. argument reordering for DATEDIFF).
    """
    from speaksql.vendor_overrides import apply_overrides as _apply

    return _apply(sql, dialect)


def _post_format(sql: str, dialect: str) -> str:
    """Final cleanup per dialect.

    Currently: strip extra blank lines, ensure trailing newline, and
    apply dialect-specific identifier-quote convention if SQLGlot
    defaulted to double-quotes where backticks / brackets are expected.
    """
    lines = [ln for ln in sql.splitlines() if ln.strip()]
    if not lines:
        return sql
    return "\n".join(lines) + "\n"


__all__ = [
    "SUPPORTED_DIALECTS",
    "Dialect",
    "Plan",
    "emit",
    "plan",
    "transpile",
]