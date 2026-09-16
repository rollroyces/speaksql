"""Vendor-specific SQL function overrides.

These are post-transpile textual fixes applied AFTER SQLGlot's emitter
for cases where the auto-generated SQL is incorrect or non-idiomatic for
the target dialect.

Each override is registered against a target dialect; when the emitter
runs for that dialect, the override gets a chance to rewrite the emitted
SQL string. Overrides should be:

1. **Narrow.** Only fix what SQLGlot gets wrong, not "improvements".
2. **Idempotent.** Re-running the override on already-fixed SQL should
   be a no-op.
3. **Pure text.** Operate on the emitted SQL string, not on the AST.
   (AST-level transforms should go in `dialects/<vendor>.py` instead.)

If a future SQLGlot release fixes the underlying bug, the override
becomes a no-op and can be deleted.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# A registered override is a function that takes the emitted SQL string
# and returns the (possibly modified) SQL string.
OverrideFn = Callable[[str], str]

_REGISTRY: dict[str, list[OverrideFn]] = {}


def register(dialect: str, fn: OverrideFn) -> None:
    """Register a post-emit override for `dialect`."""
    _REGISTRY.setdefault(dialect, []).append(fn)


def apply_overrides(sql: str, dialect: str) -> str:
    """Apply all registered overrides for `dialect` to `sql`."""
    for fn in _REGISTRY.get(dialect, ()):
        sql = fn(sql)
    return sql


# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------
# SQLGlot emits `DATE_DIFF('END_AT', start_at, CAST('second' AS DATE))`
# when given a canonical `DATEDIFF('second', start_at, end_at)` — the
# args get swapped (end first, then start, with end uppercased as a
# bare identifier) and the unit ends up in a CAST as a string literal.
# Fix: detect that pattern and rewrite to `DATE_DIFF('second', start_at, end_at)`.
# The pattern uses \w+ for the column refs and matches both upper and
# lower-case variants of the unit token.
def _duckdb_datediff(sql: str) -> str:
    # Match `DATE_DIFF('X', Y, CAST('Z' AS DATE))` where X and Z are any
    # bare identifiers (letters, digits, underscores — both `END_AT` and
    # `second` fit) and Y is any column ref. Then rewrite so that the
    # canonical DATEDIFF argument order is restored: `(unit, start, end)`.
    def _rewrite(m: re.Match) -> str:
        col_a = m.group(1)  # originally end column (now uppercased as identifier)
        col_b = m.group(2)  # originally start column
        unit = m.group(3)    # the unit literal (always a string in the CAST)
        # Lowercase the identifier-shaped ref to match the canonical input.
        col_a_l = col_a.lower()
        col_b_l = col_b.lower()
        return f"DATE_DIFF('{unit.lower()}', {col_b_l}, {col_a_l})"

    pattern = (
        r"DATE_DIFF\(\s*'([A-Za-z_][A-Za-z0-9_]*)'\s*,\s*"
        r"(\w+)\s*,\s*"
        r"CAST\(\s*'([A-Za-z_][A-Za-z0-9_]*)'\s+AS\s+DATE\s*\)\)"
    )
    return re.sub(pattern, _rewrite, sql)


# BigQuery `DATE_DIFF(end, start, unit)` → DuckDB `DATE_DIFF(unit, start, end)`.
# SQLGlot already translates the signature (it knows BQ's positional
# form vs DuckDB's unit-first form), but the start/end args get swapped
# in the process. The emitted DuckDB SQL is:
#   DATE_DIFF('UNIT', CAST(start AS DATE), CAST(end AS DATE))
# which is exactly the same broken pattern as DATEDIFF above (just
# without the explicit string literal in the third slot — it's been
# resolved to an actual CAST here). Reuse the same regex shape.
def _duckdb_date_diff(sql: str) -> str:
    def _rewrite(m: re.Match) -> str:
        col_a = m.group(1)  # original second arg (got moved first)
        col_b = m.group(2)  # original first arg (got moved second)
        unit = m.group(3)    # the unit (now uppercased as identifier)
        return f"DATE_DIFF('{unit.lower()}', {col_b.lower()}, {col_a.lower()})"

    pattern = (
        r"DATE_DIFF\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
        r"CAST\((\w+)\s+AS\s+DATE\)\s*,\s*"
        r"CAST\((\w+)\s+AS\s+DATE\)\)"
    )
    # Also normalize `'DAY'` to `'day'` so output is consistent
    # regardless of how SQLGlot initially emitted the unit.
    sql = re.sub(pattern, _rewrite, sql)
    sql = re.sub(
        r"DATE_DIFF\(\s*'([A-Z]+)'\s*,",
        lambda m: f"DATE_DIFF('{m.group(1).lower()}',",
        sql,
    )
    return sql


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------
# SQLGlot emits `CAST(AGE(CAST('second' AS TIMESTAMP),
# CAST(start_at AS TIMESTAMP)) AS BIGINT)` for `DATEDIFF('second', a, b)`
# on Postgres. The expression is malformed — AGE only takes one timestamp
# arg, so the second column (b) is dropped entirely. A pure regex fix
# can't recover both columns, and an AST-based rewrite would couple this
# module to SQLGlot internals. Best left as documented oddity: users
# working in Postgres should write `b - a` directly for time deltas.
#
# This override is intentionally a no-op safety net. If you find a
# pattern SQLGlot emits that this regex *can* safely fix, add it here.


# Postgres has no SECONDS_BETWEEN. Rewrite to `EXTRACT(EPOCH FROM (b - a))`.
# Handles the common (a, b) shape that SQLGlot passes through from the
# HANA-style 2-arg call.
def _postgres_seconds_between(sql: str) -> str:
    # SECONDS_BETWEEN(a, b) → EXTRACT(EPOCH FROM (b - a))
    return re.sub(
        r"SECONDS_BETWEEN\(\s*(\w+)\s*,\s*(\w+)\)",
        r"EXTRACT(EPOCH FROM (\2 - \1))",
        sql,
    )


# Postgres has no DAYS_BETWEEN either. Same fix.
def _postgres_days_between(sql: str) -> str:
    return re.sub(
        r"DAYS_BETWEEN\(\s*(\w+)\s*,\s*(\w+)\)",
        r"((\2)::date - (\1)::date)",
        sql,
    )


# HANA's ADD_MONTHS(d, n) is not natively supported on Postgres /
# Snowflake / MySQL. For DuckDB, SQLGlot already rewrites it to
# `d + INTERVAL n MONTH` (which works). For other dialects it stays
# as ADD_MONTHS, which the user has to handle. Override for Postgres:
# rewrite `ADD_MONTHS(col, N)` (literal N) to the portable
# `(col + (N * INTERVAL '1 month'))`. For non-literal N (column refs,
# expressions), leave it alone — that's a bigger surgery.
def _postgres_add_months(sql: str) -> str:
    sql = re.sub(
        r"ADD_MONTHS\(\s*(\w+)\s*,\s*(\d+)\s*\)",
        r"(\1 + (\2 * INTERVAL '1 month'))",
        sql,
    )
    # If SQLGlot already converted to `col + INTERVAL n MONTH`, fix that too
    # (e.g. when going Postgres → Postgres after a round-trip).
    sql = re.sub(
        r"(\w+)\s*\+\s*INTERVAL\s+(\d+)\s+MONTH",
        r"(\1 + (\2 * INTERVAL '1 month'))",
        sql,
    )
    return sql


# Snowflake also lacks ADD_MONTHS as a built-in function (it has
# DATEADD(month, N, col) instead). Same rewrite.
_snowflake_add_months = _postgres_add_months


# MySQL doesn't have ADD_MONTHS either; rewrite to DATE_ADD which it
# does have. (MySQL's DATE_ADD takes an INTERVAL literal or an
# expression — we use the same portable form as Postgres.)
def _mysql_add_months(sql: str) -> str:
    sql = re.sub(
        r"ADD_MONTHS\(\s*(\w+)\s*,\s*(\d+)\s*\)",
        r"DATE_ADD(\1, INTERVAL \2 MONTH)",
        sql,
    )
    sql = re.sub(
        r"(\w+)\s*\+\s*INTERVAL\s+(\d+)\s+MONTH",
        r"DATE_ADD(\1, INTERVAL \2 MONTH)",
        sql,
    )
    return sql


# Register built-in overrides. Use direct calls so module-level order
# doesn't matter (the @decorator syntax above would fail if applied
# before `register` was defined).
register("duckdb", _duckdb_datediff)
register("duckdb", _duckdb_date_diff)
register("postgres", _postgres_seconds_between)
register("postgres", _postgres_days_between)
register("postgres", _postgres_add_months)
register("snowflake", _snowflake_add_months)
register("mysql", _mysql_add_months)


__all__ = ["OverrideFn", "apply_overrides", "register"]