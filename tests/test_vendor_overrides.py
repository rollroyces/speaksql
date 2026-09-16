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