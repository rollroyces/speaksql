"""Core transpilation tests."""

from __future__ import annotations

import pytest
import speaksql
from speaksql import emit, plan, transpile
from speaksql.exceptions import TranspileError, UnsupportedDialectError


def test_supported_dialects_exact():
    assert speaksql.SUPPORTED_DIALECTS == frozenset(
        {"postgres", "mysql", "tsql", "snowflake", "bigquery", "spark", "duckdb", "sqlite", "hana"}
    )


def test_plan_basic():
    p = plan("SELECT 1 AS x")
    assert p.ast is not None
    sql = p.sql("postgres")
    # Pretty-printed form has the literal on a new line. Normalise whitespace.
    flat = " ".join(sql.split())
    assert "SELECT 1" in flat
    upper = flat.upper()
    assert " AS " in upper
    assert "X" in upper


def test_transpile_to_multiple_targets():
    sql = "SELECT id, amount FROM events WHERE amount > 100"
    out = transpile(
        sql,
        targets=("postgres", "mysql", "snowflake", "bigquery", "duckdb", "tsql", "spark", "sqlite", "hana"),
    )
    assert set(out.keys()) == {
        "postgres", "mysql", "snowflake", "bigquery", "duckdb", "tsql", "spark", "sqlite", "hana"
    }
    for s in out.values():
        assert "events" in s
        assert "amount" in s


def test_date_trunc_emits_per_dialect():
    sql = "SELECT DATE_TRUNC('month', created_at) AS m FROM events"
    out = transpile(sql, targets=("postgres", "mysql", "bigquery", "tsql", "spark"))
    # Postgres / DuckDB / Snowflake keep DATE_TRUNC(...)
    assert "DATE_TRUNC" in out["postgres"]
    assert "DATE_TRUNC" in out["mysql"] or "STR_TO_DATE" in out["mysql"]  # MySQL uses STR_TO_DATE
    # BigQuery uses DATE_TRUNC(<col>, MONTH)
    assert "MONTH" in out["bigquery"].upper()
    # Spark uses TRUNC(<col>, 'MONTH')
    assert "TRUNC" in out["spark"].upper()


def test_window_function():
    sql = (
        "SELECT country, ROW_NUMBER() OVER (PARTITION BY country ORDER BY amount DESC) AS rn "
        "FROM events"
    )
    out = transpile(sql, targets=("postgres", "snowflake", "tsql"))
    for s in out.values():
        assert "ROW_NUMBER" in s.upper()


def test_unsupported_dialect_raises():
    with pytest.raises(UnsupportedDialectError) as exc:
        emit(plan("SELECT 1"), "oracle")
    assert "oracle" in str(exc.value).lower()
    assert "not supported" in str(exc.value).lower()


def test_invalid_sql_raises_transpile_error():
    # A truly unparseable construct — unterminated string
    with pytest.raises(TranspileError):
        plan("SELECT 'unterminated")


def test_empty_input_raises():
    with pytest.raises(TranspileError):
        plan("")


def test_aliases_normalised():
    out = transpile("SELECT 1", targets=("mssql",))
    assert "mssql" in out
    # T-SQL output should not have backticks
    assert "`" not in out["mssql"]


def test_cte_emits_with_clause():
    sql = "WITH x AS (SELECT 1 AS a) SELECT a FROM x"
    out = transpile(sql, targets=("postgres", "snowflake"))
    for s in out.values():
        upper = s.upper()
        assert "WITH" in upper
        assert "AS (" in upper


def test_post_format_strips_blank_lines():
    sql = "SELECT\n  1\n\n\n  AS x\n\nFROM (SELECT 1) AS t"
    out = transpile(sql, targets=("postgres",))
    # No consecutive blank lines
    body = out["postgres"]
    assert "\n\n\n" not in body