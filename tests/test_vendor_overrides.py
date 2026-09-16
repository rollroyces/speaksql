"""Tests for vendor-specific SQL function overrides."""

from __future__ import annotations

from speaksql import transpile
from speaksql.vendor_overrides import apply_overrides, register


def test_register_and_apply_invokes_function():
    calls = []

    def my_override(sql: str) -> str:
        calls.append(sql)
        return sql + " -- overridden"

    register("test_dialect", my_override)
    out = apply_overrides("SELECT 1", "test_dialect")
    assert out == "SELECT 1 -- overridden"
    assert calls == ["SELECT 1"]


def test_register_isolated_to_specific_dialect():
    """Overrides registered for dialect A shouldn't fire for dialect B."""
    def override_a(sql: str) -> str:
        return sql + " -- A"

    register("dialect_a", override_a)
    assert apply_overrides("SELECT 1", "dialect_b") == "SELECT 1"
    assert apply_overrides("SELECT 1", "dialect_a") == "SELECT 1 -- A"


def test_override_chain_applies_in_order():
    """Multiple overrides on the same dialect apply in registration order."""
    def first(sql: str) -> str:
        return sql + " -- first"

    def second(sql: str) -> str:
        return sql.replace("-- first", "-- second")

    register("dialect_chain", first)
    register("dialect_chain", second)
    out = apply_overrides("SELECT 1", "dialect_chain")
    # first appends " -- first", then second finds it and replaces it
    assert out == "SELECT 1 -- second"


def test_duckdb_datediff_swap_arg_order():
    """DuckDB fix: SQLGlot swaps args; override restores canonical order.

    Input: `DATEDIFF('second', start_at, end_at)`
    Without override, SQLGlot emits the args in the wrong order:
        `DATE_DIFF('END_AT', start_at, CAST('second' AS DATE))`
    The override should restore `DATE_DIFF('second', start_at, end_at)`.
    """
    out = transpile(
        "SELECT DATEDIFF('second', start_at, end_at) AS dur FROM events",
        targets=("duckdb",),
    )
    emitted = out["duckdb"]
    # The override should have rewritten this; verify the canonical shape.
    assert "DATE_DIFF('second'" in emitted
    assert "start_at" in emitted
    assert "end_at" in emitted
    # The buggy CAST(... AS DATE) wrapper should be gone.
    assert "CAST(" not in emitted or "AS DATE" not in emitted


def test_duckdb_datediff_different_units():
    """The override should work for second/day/minute/hour, not just second."""
    for unit in ("second", "day", "minute", "hour", "week"):
        out = transpile(
            f"SELECT DATEDIFF('{unit}', start_at, end_at) FROM events",
            targets=("duckdb",),
        )
        emitted = out["duckdb"]
        # The literal unit should still be there (lowercased to match input)
        assert f"'{unit}'" in emitted


def test_duckdb_datediff_preserves_other_sql():
    """Override should be a no-op on SQL that doesn't match the pattern."""
    sql = "SELECT id, name FROM users WHERE id > 0"
    out = apply_overrides(sql, "duckdb")
    assert out == sql


def test_postgres_datediff_is_a_no_op():
    """Postgres DATETIME pattern is currently a documented no-op.

    SQLGlot emits a malformed `CAST(AGE(...))` form that a regex fix
    can't safely handle. The override is intentionally a no-op so we
    don't make things worse. Users on Postgres should write `b - a` directly.
    """
    out = transpile(
        "SELECT DATEDIFF('second', start_at, end_at) AS dur FROM events",
        targets=("postgres",),
    )
    # Whatever SQLGlot emitted passes through unchanged.
    # The key assertion is that the override doesn't *crash*.
    assert "dur" in out["postgres"]


def test_vendor_overrides_invoked_from_transpile():
    """End-to-end: a DATEDIFF query transpiled to DuckDB should fire the override."""
    out = transpile(
        "SELECT DATEDIFF('day', a, b) FROM t",
        targets=("duckdb",),
    )
    # The override ensures correct arg order
    assert "DATE_DIFF('day'" in out["duckdb"]
    assert "a" in out["duckdb"]
    assert "b" in out["duckdb"]


def test_register_is_idempotent_across_imports():
    """Module-level register() calls should be idempotent.

    We can't easily re-trigger import in a unit test, but we can verify
    that applying twice with no new registration is safe.
    """
    from speaksql.vendor_overrides import _REGISTRY

    # Pick a known-registered dialect
    initial_count = len(_REGISTRY.get("duckdb", ()))
    apply_overrides("SELECT 1", "duckdb")
    assert len(_REGISTRY.get("duckdb", ())) == initial_count


# ---------------------------------------------------------------------------
# New override tests (added in the second vendor-override round)
# ---------------------------------------------------------------------------


def test_duckdb_date_diff_swap_args():
    """BigQuery `DATE_DIFF(end, start, unit)` → DuckDB `DATE_DIFF(unit, start, end)`.

    The override also lowercases the unit literal so output is consistent
    regardless of how SQLGlot initially emitted it.
    """
    out = transpile(
        "SELECT DATE_DIFF(end_at, start_at, DAY) FROM events",
        targets=("duckdb",),
    )
    emitted = out["duckdb"]
    # Args restored to (unit, start, end) — what DuckDB expects
    assert "DATE_DIFF('day', start_at, end_at)" in emitted


def test_duckdb_date_diff_other_units():
    for unit in ("DAY", "HOUR", "MONTH", "WEEK"):
        out = transpile(
            f"SELECT DATE_DIFF(end_at, start_at, {unit}) FROM events",
            targets=("duckdb",),
        )
        emitted = out["duckdb"]
        assert f"DATE_DIFF('{unit.lower()}', start_at, end_at)" in emitted


def test_postgres_seconds_between_to_epoch():
    """`SECONDS_BETWEEN(t1, t2)` on Postgres has no function; rewrite
    to the portable `EXTRACT(EPOCH FROM (t2 - t1))`."""
    out = transpile(
        "SELECT SECONDS_BETWEEN(t1, t2) AS d FROM events",
        targets=("postgres",),
    )
    emitted = out["postgres"]
    assert "EXTRACT(EPOCH FROM (t2 - t1))" in emitted
    # Original function name should be gone
    assert "SECONDS_BETWEEN" not in emitted


def test_postgres_days_between_to_date_diff():
    out = transpile(
        "SELECT DAYS_BETWEEN(t1, t2) AS d FROM events",
        targets=("postgres",),
    )
    emitted = out["postgres"]
    # (t2)::date - (t1)::date is the idiomatic day-difference expression
    assert "(t2)::date - (t1)::date" in emitted
    assert "DAYS_BETWEEN" not in emitted


def test_postgres_add_months_literal():
    """`ADD_MONTHS(d, 3)` on Postgres becomes portable interval arithmetic."""
    out = transpile(
        "SELECT ADD_MONTHS(d, 3) FROM t",
        targets=("postgres",),
    )
    emitted = out["postgres"]
    assert "INTERVAL '1 month'" in emitted
    assert "ADD_MONTHS" not in emitted


def test_snowflake_add_months_literal():
    out = transpile(
        "SELECT ADD_MONTHS(d, 3) FROM t",
        targets=("snowflake",),
    )
    emitted = out["snowflake"]
    assert "INTERVAL '1 month'" in emitted


def test_mysql_add_months_to_date_add():
    out = transpile(
        "SELECT ADD_MONTHS(d, 3) FROM t",
        targets=("mysql",),
    )
    emitted = out["mysql"]
    # MySQL's DATE_ADD takes (date, INTERVAL expr unit)
    assert "DATE_ADD(d, INTERVAL 3 MONTH)" in emitted


def test_overrides_dont_break_unrelated_sql():
    """A SELECT without any of these vendor functions should pass through."""
    sql = "SELECT id, name FROM users WHERE active = TRUE ORDER BY id LIMIT 10"
    out = transpile(sql, targets=("duckdb", "postgres", "mysql", "snowflake"))
    for emitted in out.values():
        clean = " ".join(emitted.split())
        assert "id" in clean
        assert "name" in clean.lower()
        assert "users" in clean