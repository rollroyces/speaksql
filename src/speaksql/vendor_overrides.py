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


# Register built-in overrides. Use direct calls so module-level order
# doesn't matter (the @decorator syntax above would fail if applied
# before `register` was defined).
register("duckdb", _duckdb_datediff)


__all__ = ["OverrideFn", "apply_overrides", "register"]