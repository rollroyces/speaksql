"""Tests for the vendor HANA dialect."""

from __future__ import annotations

import pytest
import speaksql
import sqlglot
from speaksql.dialects.hana import Hana, _register


def test_hana_registered_in_sqlglot():
    _register()  # idempotent
    assert "hana" in sqlglot.dialects.dialect.Dialect.classes


def test_hana_class_inherits_postgres():
    """HANA extends Postgres; the parser/generator tokenizers are reused."""
    from sqlglot.dialects.postgres import Postgres

    assert issubclass(Hana, Postgres)


def test_hana_simple_transpile():
    out = speaksql.transpile("SELECT id, name FROM t WHERE id > 0", targets=("hana",))
    assert "hana" in out
    # HANA inherits Postgres output shape
    assert "SELECT" in out["hana"].upper()
    assert "FROM t" in out["hana"]


def test_hana_postgres_compatible_output():
    """For ANSI SQL, HANA and Postgres emit the same shape."""
    canonical = (
        "SELECT DATE_TRUNC('month', created_at) AS m, SUM(amount) AS total "
        "FROM events GROUP BY m ORDER BY m"
    )
    hana_sql = speaksql.transpile(canonical, targets=("hana",))["hana"]
    pg_sql = speaksql.transpile(canonical, targets=("postgres",))["postgres"]
    # Strip whitespace for comparison
    assert hana_sql.split() == pg_sql.split()


def test_hana_add_months_parses():
    """ADD_MONTHS is HANA-specific — verifies our dialect accepts it."""
    sql = "SELECT ADD_MONTHS('2026-01-01', 3) AS d FROM t"
    _register()
    ast = sqlglot.parse_one(sql, dialect="hana")
    out = ast.sql(dialect="hana")
    assert "ADD_MONTHS" in out.upper()


def test_hana_map_with_4_args():
    """HANA MAP takes variable even-arity; Postgres 2-arity binding must NOT apply."""
    sql = "SELECT MAP('k1', 'v1', 'k2', 'v2') AS m FROM t"
    _register()
    ast = sqlglot.parse_one(sql, dialect="hana")
    out = ast.sql(dialect="hana")
    assert "MAP" in out.upper()
    assert "k1" in out


def test_hana_to_varchar_parses():
    sql = "SELECT TO_VARCHAR(x, 'YYYY-MM-DD') AS s FROM t"
    _register()
    ast = sqlglot.parse_one(sql, dialect="hana")
    out = ast.sql(dialect="hana")
    assert "TO_VARCHAR" in out.upper()


def test_hana_days_between_parses():
    sql = "SELECT DAYS_BETWEEN('2026-01-01', '2026-12-31') AS n FROM t"
    _register()
    ast = sqlglot.parse_one(sql, dialect="hana")
    out = ast.sql(dialect="hana")
    assert "DAYS_BETWEEN" in out.upper()


def test_hana_via_saphana_alias():
    """`saphana` should resolve to the HANA dialect."""
    out = speaksql.transpile("SELECT 1", targets=("saphana",))
    assert "saphana" in out


def test_hana_does_not_break_other_dialects():
    """Importing HANA must not affect other dialects' transpile output."""
    canonical = "SELECT DATE_TRUNC('month', x) AS m FROM t"
    expected_postgres = speaksql.transpile(canonical, targets=("postgres",))["postgres"]
    # Trigger HANA registration
    _register()
    # Postgres output should be unchanged
    assert speaksql.transpile(canonical, targets=("postgres",))["postgres"] == expected_postgres


def test_hana_series_generate_date_parses():
    """SERIES_GENERATE_DATE is HANA-specific; verify parse + emit round-trip.

    HANA inherits Postgres parsing — `SERIES_GENERATE_DATE` falls through
    to the anonymous-function path and survives a parse→emit round-trip.
    """
    sql = (
        "SELECT GENERATED_PERIOD_START, GENERATED_PERIOD_END "
        "FROM SERIES_GENERATE_DATE('2026-01-01', '2026-12-31', 'MONTH', 1)"
    )
    _register()
    ast = sqlglot.parse_one(sql, dialect="hana")
    out = ast.sql(dialect="hana")
    assert "SERIES_GENERATE_DATE" in out.upper()
    assert "2026-01-01" in out
    assert "2026-12-31" in out


def test_hana_add_days_and_seconds_parse():
    """ADD_DAYS / ADD_SECONDS are HANA date arithmetic; must round-trip verbatim."""
    for fn, arg in [("ADD_DAYS", "7"), ("ADD_SECONDS", "60")]:
        sql = f"SELECT {fn}('2026-01-01', {arg}) AS d FROM t"
        _register()
        ast = sqlglot.parse_one(sql, dialect="hana")
        out = ast.sql(dialect="hana")
        assert fn in out.upper(), f"lost {fn} in round-trip"


def test_hana_transpile_then_reparse_yields_same_ast():
    """Full canonical→HANA→parse→emit round-trip must preserve column refs."""
    canonical = (
        "SELECT id, name, created_at "
        "FROM users "
        "WHERE created_at > DATE '2026-01-01' "
        "ORDER BY created_at"
    )
    hana_sql = speaksql.transpile(canonical, targets=("hana",))["hana"]
    _register()
    reparsed = sqlglot.parse_one(hana_sql, dialect="hana")
    out_again = reparsed.sql(dialect="hana")
    assert " ".join(out_again.split()) == " ".join(hana_sql.split())


def test_hana_rejects_malformed_sql():
    """HANA dialect must surface parse errors, not silently emit garbage."""
    from sqlglot.errors import ParseError
    _register()
    with pytest.raises(ParseError):
        sqlglot.parse_one("SELECT FROM WHERE", dialect="hana")