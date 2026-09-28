"""Record a SpeakSQL CLI session into an asciinema v2 .cast file.

asciinema v2 format — line-delimited JSON:

  {"version": 2, "width": 80, "height": 24, ...}     ← line 1 (header)
  [0.123, "o", "echo hello\\n"]                     ← output frame
  [0.456, "o", "more output"]

The whole file is human-readable JSONL. Plays back in any static site
via the ~30-line <asciinema-player> widget bundled in `docs/player.js`.

Usage:
    python scripts/record_demo.py <output.cast> <script_name>

The script_name chooses which demo to record. Available scripts:

    - demo            — NL → 3 dialects + format (the headline pitch)
    - format          — pretty / compact / cross-dialect canonicalize
    - repl            — interactive REPL with :dialects switching
    - diff            — semantic diff in human-readable + JSON
    - graph           — JOIN graph CLI on a SQLite DB
    - vendor_overrides — DuckDB DATEDIFF fix (per-dialect function override)
"""

from __future__ import annotations

import json
import os
import pty
import select
import sys
import time
from pathlib import Path

COLS = 100
ROWS = 28
DELAY = 0.5  # seconds between commands

# Each entry: (command, comment shown above the prompt)
COMMANDS_BY_NAME: dict[str, list[tuple[str, str]]] = {
    "demo": [
        (
            "echo 'monthly amount by country' | .venv/bin/speaksql ask " +
            "-d postgres -d bigquery -d duckdb 2>&1 | head -40",
            "Plain English → 3 dialects in one shot.",
        ),
        (
            ".venv/bin/speaksql format --target postgres " +
            "'select    a,b,  c FROM tbl WHERE x=1' 2>&1",
            "Canonicalize SQL via the format subcommand.",
        ),
    ],
    "format": [
        (
            "echo 'select    a,b,  c FROM tbl WHERE x=1' | .venv/bin/speaksql " +
            "format --target postgres 2>&1",
            "Compact form (single line).",
        ),
        (
            ".venv/bin/speaksql format --pretty " +
            "'SELECT id, name, email FROM users WHERE active = TRUE "
            "ORDER BY id LIMIT 10' 2>&1 | head -15",
            "Pretty-print mode (multi-line).",
        ),
        (
            ".venv/bin/speaksql format --target snowflake --dialect duckdb " +
            "'SELECT NOW()' 2>&1",
            "Convert DuckDB → Snowflake.",
        ),
    ],
    "repl": [
        (
            "printf 'SELECT DATE_TRUNC('\\''month'\\'', created_at) AS m, " +
            "country, SUM(amount) FROM events GROUP BY m\\n"
            ":dialects duckdb\\n:quit\\n' " +
            "| .venv/bin/speaksql repl --dialect postgres --dialect bigquery "
            "2>&1 | head -25",
            "Live REPL with runtime dialect switching.",
        ),
    ],
    "diff": [
        (
            ".venv/bin/speaksql diff " +
            "'SELECT id FROM t ORDER BY id ASC NULLS LAST' "
            "'SELECT id FROM t ORDER BY id ASC' 2>&1",
            "Semantic diff (human-readable).",
        ),
        (
            ".venv/bin/speaksql diff --json " +
            "'SELECT id FROM t ORDER BY id ASC NULLS LAST' "
            "'SELECT id FROM t ORDER BY id ASC' 2>&1",
            "Semantic diff (JSON for tooling).",
        ),
    ],
    "graph": [
        (
            ".venv/bin/speaksql graph /tmp/sample.sqlite --json 2>&1 | head -20",
            "JOIN graph CLI — JSON dump of nodes + edges.",
        ),
        (
            ".venv/bin/speaksql graph /tmp/sample.sqlite --html /tmp/graph.html 2>&1",
            "Render the same graph as an interactive HTML page.",
        ),
    ],
    "vendor_overrides": [
        (
            ".venv/bin/speaksql format --target duckdb " +
            "'SELECT DATE_DIFF('\\''second'\\'', end_at, start_at) AS elapsed " +
            "FROM events' 2>&1",
            "Raw SQLGlot emit — DuckDB's DATE_DIFF args are reversed.",
        ),
        (
            ".venv/bin/python -c \"import duckdb; con = duckdb.connect('/tmp/events.duck'); " +
            "con.execute('CREATE TABLE events (start_at TIMESTAMP, end_at TIMESTAMP)'); " +
            "con.execute(\\\"INSERT INTO events VALUES (TIMESTAMP '2026-01-15 10:00:00', TIMESTAMP '2026-01-22 10:00:00')\\\"); " +
            "con.execute(\\\"INSERT INTO events VALUES (TIMESTAMP '2026-02-01 09:00:00', TIMESTAMP '2026-02-08 09:00:00')\\\"); " +
            "print('seeded', con.execute('SELECT COUNT(*) FROM events').fetchone()[0], 'rows')\" 2>&1",
            "Seed a DuckDB file with two timestamped rows.",
        ),
        (
            "echo \"SELECT DATE_DIFF('second', end_at, start_at) AS elapsed FROM events\" " +
            "| .venv/bin/speaksql ask --sql --dialect duckdb --execute-on duckdb " +
            "--db-path /tmp/events.duck 2>&1 | tail -15",
            "Vendor override flips the args AND runs the query on DuckDB.",
        ),
    ],
}


def stamp(events: list, start: float, kind: str, text: str) -> None:
    events.append([round(time.monotonic() - start, 3), kind, text])


def make_prompt() -> str:
    return "\x1b[1;32m$\x1b[0m "


def _record_one(name: str, commands: list[tuple[str, str]]) -> tuple[int, list]:
    """Record a single demo and return (frame_count, events)."""
    COLS, ROWS = 100, 28
    start = time.monotonic()
    events: list = []

    # Header: clear screen, banner, then run each command.
    stamp(events, start, "o", "\x1b[H\x1b[2J")
    stamp(events, start, "o", "\x1b[?25l")
    banner = (
        f"\x1b[1;36mSpeakSQL \u2014 {name}\x1b[0m  "
        f"\x1b[2m# recorded demo\x1b[0m\n"
    )
    stamp(events, start, "o", banner)

    # Suppress macOS first-run banner by clearing BASH_ENV / ENV.
    # The banner comes from /private/etc/bashrc_Apple_Terminal which
    # Apple's bash sources on first invocation.
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["COLUMNS"] = str(COLS)
        os.environ["LINES"] = str(ROWS)
        os.environ["TERM"] = "xterm-256color"
        os.environ["SPEAKSQL_USE_LLM"] = "0"
        os.environ["PS1"] = "$ "
        # Apple-specific: tell bash not to source the system bashrc.
        os.environ["BASH_ENV"] = "/dev/null"
        os.execvp(
            "bash",
            ["bash", "--norc", "--noprofile", "-i"],
        )

    # Lines that come from macOS's interactive-first-time banner; strip them.
    BANNER_PATTERNS = (
        "The default interactive shell is now zsh.",
        "To update your account to use zsh, please run",
        "For more details, please visit",
        "https://support.apple.com/kb/HT208050",
    )

    try:
        # Disable terminal echo so input we write doesn't get echoed back
        # by the shell (which would visually duplicate the command line).
        os.write(fd, b"stty -echo\n")
        # Drain the acknowledgement
        last_data = time.monotonic()
        while True:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    os.read(fd, 4096)
                except OSError:
                    break
                last_data = time.monotonic()
            elif (time.monotonic() - last_data) > 0.5:
                break
        for cmd, comment in commands:
            # Render the comment + prompt + command as visible output
            stamp(events, start, "o", f"\x1b[2m# {comment}\x1b[0m\n")
            stamp(events, start, "o", "\x1b[1;32m$\x1b[0m " + cmd + "\n")
            os.write(fd, (cmd + "\n").encode())
            last_data = time.monotonic()
            while True:
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace").replace(
                        "\r\n", "\n"
                    )
                    # Drop macOS first-run banner lines.
                    cleaned = "\n".join(
                        line for line in text.split("\n")
                        if not any(p in line for p in BANNER_PATTERNS)
                    )
                    if cleaned:
                        stamp(events, start, "o", cleaned)
                    last_data = time.monotonic()
                elif (time.monotonic() - last_data) > 0.8:
                    break
            time.sleep(DELAY)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
    return len(events), events


def _seed_sample_sqlite() -> None:
    """Seed /tmp/sample.sqlite with a 4-table FK schema used by `graph` demo."""
    import sqlite3

    db = "/tmp/sample.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT,
            created_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY, order_id INTEGER, sku TEXT, qty INTEGER,
            price REAL, FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY, sku TEXT UNIQUE, name TEXT, price REAL
        );
        """
    )
    con.close()


def _seed_events_duckdb() -> None:
    """Seed /tmp/events.duck with a 2-row events table used by
    `vendor_overrides` demo. Re-creates on each call."""
    import os

    import duckdb  # type: ignore[import-not-found]  # scripts/ outside src/

    db = "/tmp/events.duck"
    # Remove any pre-existing file so we always start from a clean schema.
    # DuckDB's `CREATE TABLE IF NOT EXISTS` would silently skip the recreate
    # which leaves stale rows in a previously-recorded session.
    if os.path.exists(db):
        os.unlink(db)
    con = duckdb.connect(db)
    con.execute(
        "CREATE TABLE events (start_at TIMESTAMP, end_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO events VALUES "
        "(TIMESTAMP '2026-01-15 10:00:00', TIMESTAMP '2026-01-22 10:00:00'),"
        " (TIMESTAMP '2026-02-01 09:00:00', TIMESTAMP '2026-02-08 09:00:00')"
    )
    con.close()


# Some demos need fixture DBs to be present before recording.
# This runs the seed before the recording starts so the demo captures
# the right output.
_PRE_RECORD_HOOKS = {
    "graph": [_seed_sample_sqlite],
    "vendor_overrides": [_seed_events_duckdb],
}


def record(name: str, script_file: str | Path) -> None:
    commands = COMMANDS_BY_NAME.get(name)
    if not commands:
        print(f"No script named {name!r}. Options: {list(COMMANDS_BY_NAME)}")
        sys.exit(1)

    # Run any pre-record hooks (seed fixture DBs etc.)
    for hook in _PRE_RECORD_HOOKS.get(name, []):
        hook()

    n_frames, events = _record_one(name, commands)

    header = {
        "version": 2,
        "width": 100,
        "height": 28,
        "timestamp": int(time.time()),
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
        "title": f"SpeakSQL \u2014 {name}",
    }
    out_path = Path(script_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        f.write(json.dumps(header) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    total = events[-1][0] if events else 0
    print(f"Wrote {out_path} ({n_frames} frames, {round(total, 1)}s)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/demo.cast"
    name = sys.argv[2] if len(sys.argv) > 2 else "demo"
    record(name, out)