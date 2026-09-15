"""CLI tests via Click's testing runner."""

from __future__ import annotations

from click.testing import CliRunner
from speaksql.cli import main


def test_cli_dialects_command():
    r = CliRunner().invoke(main, ["dialects"])
    assert r.exit_code == 0
    assert "postgres" in r.output
    assert "snowflake" in r.output


def test_cli_ask_with_canonical_sql():
    r = CliRunner().invoke(
        main,
        ["ask", "-d", "postgres", "-d", "snowflake", "--sql", "SELECT 1 AS x"],
    )
    assert r.exit_code == 0
    flat = " ".join(r.output.split())
    upper = flat.upper()
    assert "SELECT 1" in flat
    assert " AS " in upper
    assert "X" in upper


def test_cli_ask_with_nl():
    r = CliRunner().invoke(main, ["ask", "-d", "postgres", "monthly amount by country"])
    assert r.exit_code == 0
    assert "DATE_TRUNC" in r.output.upper()


def test_cli_ask_json():
    r = CliRunner().invoke(main, ["ask", "-d", "postgres", "--json", "--sql", "SELECT 1"])
    assert r.exit_code == 0
    import json
    parsed = json.loads(r.output)
    assert parsed["canonical"] == "SELECT 1"
    assert "postgres" in parsed["results"]


def test_cli_missing_question_errors():
    r = CliRunner().invoke(main, ["ask"])
    assert r.exit_code != 0


def test_cli_ask_execute_duckdb(tmp_path):
    """Execute the transpiled SQL on a seeded DuckDB file."""
    if not __import__("importlib").util.find_spec("duckdb"):
        import pytest
        pytest.skip("duckdb not installed")

    import duckdb

    db = tmp_path / "events.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE events (id INTEGER, country TEXT, amount DOUBLE, created_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO events VALUES (1, 'JP', 100.0, '2026-01-15'), (2, 'US', 150.0, '2026-02-20')"
    )
    con.close()

    r = CliRunner().invoke(
        main,
        [
            "ask",
            "-d",
            "duckdb",
            "--sql",
            "SELECT DATE_TRUNC('month', created_at) AS m, SUM(amount) AS total FROM events GROUP BY m ORDER BY m",
            "--execute-on",
            "duckdb",
            "--db-path",
            str(db),
            "--json",
        ],
    )
    assert r.exit_code == 0
    import json
    parsed = json.loads(r.output)
    assert parsed["execution"]["executed"] is True
    assert parsed["execution"]["backend"] == "duckdb"
    assert parsed["execution"]["row_count"] == 2


def test_cli_diff_identical():
    r = CliRunner().invoke(
        main,
        [
            "diff",
            "SELECT id FROM t WHERE id > 0",
            "SELECT id FROM t WHERE id > 0",
        ],
    )
    assert r.exit_code == 0
    assert "No semantic differences" in r.output


def test_cli_diff_with_null_ordering():
    r = CliRunner().invoke(
        main,
        [
            "diff",
            "SELECT id FROM t ORDER BY id ASC NULLS LAST",
            "SELECT id FROM t ORDER BY id ASC",
            "--json",
        ],
    )
    assert r.exit_code == 0
    import json
    parsed = json.loads(r.output)
    assert any(d["category"] == "null_ordering" for d in parsed)


def test_cli_diff_with_dialect():
    r = CliRunner().invoke(
        main,
        [
            "diff",
            "-d",
            "tsql",
            "postgres",
            "SELECT TOP 10 id FROM t",
            "SELECT id FROM t LIMIT 10",
        ],
    )
    assert r.exit_code == 0
    assert "No semantic differences" in r.output