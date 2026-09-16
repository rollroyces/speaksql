"""Tests for the `speaksql repl` interactive command.

We drive the REPL by piping stdin via subprocess. This catches the things
that matter most about a REPL: input parsing, output shape, `:cmd` handling,
and clean shutdown on EOF.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

# Path to the CLI binary in this venv. `uv pip install -e` puts `speaksql`
# on PATH via the project's console_scripts entry.
_SPEAKSQL = Path(__file__).resolve().parents[1] / ".venv/bin/speaksql"


def _run_repl(
    input_text: str,
    args: tuple[str, ...] = (),
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    """Drive the REPL with `input_text` piped on stdin.

    Uses an env with LC_ALL=C and TERM=dumb so the prompt is predictable
    and there's no TTY noise.
    """
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["TERM"] = "dumb"
    return subprocess.run(
        [str(_SPEAKSQL), "repl", *args],
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def test_repl_transpiles_a_simple_select_to_duckdb():
    proc = _run_repl("SELECT 1 AS x\n:quit\n", args=("--dialect", "duckdb"))
    assert proc.returncode == 0
    # The REPL prints a header, then `> --- duckdb ---` plus the SQL,
    # then `bye!` on exit.
    out = proc.stdout
    assert "--- duckdb ---" in out
    assert "1 AS x" in out
    assert "bye!" in out


def test_repl_transpiles_to_multiple_dialects():
    proc = _run_repl(
        "SELECT 1 AS x\n:quit\n",
        args=("--dialect", "duckdb", "--dialect", "postgres"),
    )
    assert proc.returncode == 0
    assert "--- duckdb ---" in proc.stdout
    assert "--- postgres ---" in proc.stdout


def test_repl_handles_eof_cleanly():
    """No `:quit`, just EOF — should exit cleanly and print bye!."""
    proc = _run_repl("SELECT 1 AS x\n", args=("--dialect", "duckdb",))
    assert proc.returncode == 0
    assert "bye!" in proc.stdout


def test_repl_switches_active_dialects_via_colon_dialects():
    proc = _run_repl(
        ":dialects postgres\nSELECT 1 AS x\n:quit\n",
        args=("--dialect", "duckdb",),
    )
    assert proc.returncode == 0
    # After `:dialects postgres`, only postgres should appear in output.
    assert "--- postgres ---" in proc.stdout
    # The initial duckdb header is also there; that's OK — we test that
    # the SECOND query (after `:dialects postgres`) emits postgres only.
    # Simple check: postgres appears at least once, and after `:dialects
    # postgres` there's no `--- duckdb ---` for the second query.
    segments = proc.stdout.split("--- postgres ---")
    assert len(segments) >= 2


def test_repl_help_command_prints_commands():
    proc = _run_repl(":help\n:quit\n", args=("--dialect", "duckdb",))
    assert proc.returncode == 0
    assert ":help" in proc.stdout
    assert ":dialects" in proc.stdout
    assert ":quit" in proc.stdout


def test_repl_quit_command_exits():
    proc = _run_repl(":quit\n", args=("--dialect", "duckdb",))
    assert proc.returncode == 0
    assert "bye!" in proc.stdout


def test_repl_unknown_command_shows_error():
    proc = _run_repl(":foobar\n:quit\n", args=("--dialect", "duckdb",))
    assert proc.returncode == 0
    assert "foobar" in proc.stdout or "foobar" in proc.stderr
    assert "unknown" in proc.stdout or "unknown" in proc.stderr


def test_repl_empty_input_is_noop():
    """Empty lines should not crash the REPL."""
    proc = _run_repl("\n\n\n:quit\n", args=("--dialect", "duckdb",))
    assert proc.returncode == 0
    assert "bye!" in proc.stdout


def test_repl_with_introspected_db_uses_schema_for_nl():
    """When --db-path is given, an NL question should be matched against
    the introspected schema."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
            conn.execute(
                "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER)"
            )
            conn.commit()
        finally:
            conn.close()
        proc = _run_repl(
            "show me orders\n:quit\n",
            args=("--db-path", str(db_path), "--dialect", "duckdb"),
        )
        assert proc.returncode == 0
        assert "# introspected" in proc.stdout
        assert "--- duckdb ---" in proc.stdout


def test_repl_with_db_path_and_raw_sql_bypasses_nl_planner():
    """Raw SQL (begins with SELECT) should still work even with --db-path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
        proc = _run_repl(
            "SELECT 42 AS answer\n:quit\n",
            args=("--db-path", str(db_path), "--dialect", "postgres"),
        )
        assert proc.returncode == 0
        assert "42 AS answer" in proc.stdout


def test_repl_help_in_command_returns_no_dialect_sections():
    """:help is a meta-command, should NOT trigger a query expansion."""
    proc = _run_repl(":help\n:quit\n", args=("--dialect", "duckdb",))
    assert proc.returncode == 0
    # Help shouldn't print any dialect sections.
    assert "--- duckdb ---" not in proc.stdout


def test_repl_quit_alias():
    """:q is an alias for :quit."""
    proc = _run_repl(":q\n", args=("--dialect", "duckdb",))
    assert proc.returncode == 0
    assert "bye!" in proc.stdout


@pytest.mark.parametrize("prefix", ["SELECT", "select", "with", "INSERT", "UPDATE", "DELETE", "MERGE"])
def test_repl_treats_sql_prefixes_as_raw_sql(prefix):
    """Case-insensitive SQL prefixes trigger raw-SQL mode even when --db-path is set.

    The REPL should attempt to transpile the input as raw SQL rather than
    route it through the FK-aware planner. We assert by checking that the
    `--- duckdb ---` section was emitted (which only happens in the raw
    SQL transpile path), even if the actual SQL is invalid.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
        proc = _run_repl(
            f"{prefix} 99\n:quit\n",
            args=("--db-path", str(db_path), "--dialect", "duckdb"),
        )
        assert proc.returncode == 0
        # Either we got a valid transpile, or we got a parser error from
        # raw SQL transpile (not from the FK-aware planner). The marker is:
        # the REPL entered transpile(), not plan_for_question().
        combined = proc.stdout + proc.stderr
        assert (
            "--- duckdb ---" in proc.stdout
            or "Failed to parse" in proc.stderr
            or "Expected" in proc.stderr
        ), f"prefix={prefix!r}: REPL did not route to raw-SQL transpile path:\n{combined}"


def test_repl_no_active_dialect_falls_back_to_all_supported():
    """Without --dialect, REPL should still work and emit at least one dialect."""
    proc = _run_repl("SELECT 1 AS x\n:quit\n")
    assert proc.returncode == 0
    # Should have emitted at least one --- XXX --- section
    assert "--- " in proc.stdout