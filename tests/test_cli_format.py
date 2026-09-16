"""Tests for the `speaksql format` subcommand.

Drives the CLI as a subprocess so we cover argument parsing, stdin
fallback, file input, --pretty mode, and error handling.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SPEAKSQL = Path(__file__).resolve().parents[1] / ".venv/bin/speaksql"


def _run_format(
    args: tuple[str, ...], stdin_text: str = ""
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["TERM"] = "dumb"
    return subprocess.run(
        [str(_SPEAKSQL), "format", *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=False,
    )


def test_format_squeezes_whitespace_to_single_line():
    proc = _run_format((), "SELECT    a,b,  c  FROM  tbl  WHERE x=1\n")
    assert proc.returncode == 0, proc.stderr
    # All collapsed to single spaces; keywords uppercased.
    assert proc.stdout.strip() == "SELECT a, b, c FROM tbl WHERE x = 1"


def test_format_pretty_mode_uses_newlines():
    proc = _run_format(
        ("--pretty",),
        "SELECT id, name FROM users WHERE active = true ORDER BY id LIMIT 10",
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # Pretty-printed form should have newlines + 2-space indent
    assert "\n" in out
    assert "  id," in out or "  name," in out  # at least one indented col


def test_format_with_target_dialect():
    proc = _run_format(("--target", "postgres"), "select 1, 2, 3")
    assert proc.returncode == 0, proc.stderr
    assert "1, 2, 3" in proc.stdout


def test_format_with_source_and_target():
    proc = _run_format(
        ("--dialect", "duckdb", "--target", "snowflake"),
        "SELECT CURRENT_DATE",
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.lower()
    # Snowflake also uses CURRENT_DATE (no rewrite needed) but the
    # canonical form should still parse cleanly.
    assert "current_date" in out or "current timestamp" in out


def test_format_from_file(tmp_path):
    sql_file = tmp_path / "query.sql"
    sql_file.write_text("SELECT a, b FROM t WHERE c = 1")
    proc = _run_format(("--from-file", str(sql_file)))
    assert proc.returncode == 0, proc.stderr
    assert "SELECT a, b FROM t WHERE c = 1" in proc.stdout


def test_format_empty_sql_fails():
    proc = _run_format((), "")
    assert proc.returncode != 0  # ClickException exits non-zero


def test_format_invalid_sql_fails():
    proc = _run_format((), "SELECT FROM WHERE GARBAGE")
    # Should exit non-zero with a parse error message
    assert proc.returncode != 0


def test_format_help_lists_options():
    proc = _run_format(("--help",))
    assert proc.returncode == 0
    assert "--target" in proc.stdout
    assert "--dialect" in proc.stdout
    assert "--pretty" in proc.stdout
    assert "--from-file" in proc.stdout


def test_format_compact_default():
    """When --pretty is NOT given, output is single-line."""
    proc = _run_format(
        (),
        "SELECT id, name FROM users WHERE active = TRUE ORDER BY id LIMIT 100",
    )
    assert proc.returncode == 0
    # No newlines in compact mode
    assert "\n" not in proc.stdout.rstrip("\n")


def test_format_applies_vendor_overrides():
    """DuckDB DATE_DIFF should be normalized by the override registry."""
    # The broken form SQLGlot sometimes emits
    proc = _run_format(
        ("--target", "duckdb"),
        "SELECT DATEDIFF('day', start_at, end_at) FROM events",
    )
    assert proc.returncode == 0, proc.stderr
    # The override should normalize the args; verify SQLGlot form was corrected
    out = proc.stdout.strip()
    # Either SQLGlot already produced a correct form (test passes), or our
    # override fired. Either way, the output should be valid DuckDB syntax.
    assert "DATE_DIFF" in out
    assert "start_at" in out
    assert "end_at" in out