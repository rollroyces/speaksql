"""End-to-end roundtrip: canonical SQL → DuckDB, SQLite, and verify it runs."""

from __future__ import annotations

import pytest
import speaksql
from speaksql.backends import SQLiteBackend


@pytest.fixture
def sqlite_backend():
    b = SQLiteBackend(":memory:")
    b._conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, event_name TEXT, country TEXT, amount REAL, created_at TEXT)"
    )
    b._conn.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        [
            (1, "click", "US", 10.0, "2024-01-15"),
            (2, "buy",    "US", 50.0, "2024-02-15"),
            (3, "click",  "DE",  5.0, "2024-01-20"),
            (4, "buy",    "DE", 25.0, "2024-02-22"),
            (5, "click",  "JP",  3.0, "2024-03-01"),
        ],
    )
    yield b
    b.close()


def test_canonical_runs_on_sqlite(sqlite_backend):
    canonical = "SELECT country, SUM(amount) AS total FROM events GROUP BY country ORDER BY total DESC"
    out = speaksql.transpile(canonical, targets=("sqlite",))
    sqlite_sql = out["sqlite"]
    rows = sqlite_backend.execute(sqlite_sql)
    countries = [r[0] for r in rows]
    assert countries == ["US", "DE", "JP"]  # US 60, DE 30, JP 3
    assert rows[0][1] == 60.0