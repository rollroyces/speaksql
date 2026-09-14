"""End-to-end demo: one canonical query, every dialect.

Run from the project root:

    PYTHONPATH=src python examples/demo_all_dialects.py
"""

from __future__ import annotations

import speaksql


def main() -> None:
    canonical = (
        "SELECT "
        "  DATE_TRUNC('month', created_at) AS month, "
        "  country, "
        "  SUM(amount) AS total, "
        "  COUNT(*) AS events "
        "FROM events "
        "WHERE amount > 0 "
        "GROUP BY month, country "
        "ORDER BY month, total DESC"
    )

    print(f"Canonical ANSI SQL:\n{canonical}\n")
    out = speaksql.transpile(
        canonical,
        targets=("postgres", "mysql", "snowflake", "bigquery", "tsql", "duckdb", "spark", "sqlite", "hana"),
    )
    for dialect, sql in out.items():
        print(f"--- {dialect} ---")
        print(sql)


if __name__ == "__main__":
    main()