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
    assert "postgres" in parsed


def test_cli_missing_question_errors():
    r = CliRunner().invoke(main, ["ask"])
    assert r.exit_code != 0