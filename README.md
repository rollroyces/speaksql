# SpeakSQL

> **Speak once. Query any dialect.**

A dialect-aware SQL transpiler for Python. Build a canonical ANSI query plan
once, emit correct syntax for **PostgreSQL, MySQL, SQL Server, Snowflake,
BigQuery, Databricks, DuckDB, SQLite,** and **SAP HANA** — without writing
per-dialect SQL by hand.

[![CI](https://github.com/rollroyces/speaksql/actions/workflows/ci.yml/badge.svg)](https://github.com/rollroyces/speaksql/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/rollroyces/speaksql)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](LICENSE)
[![Built on SQLGlot](https://img.shields.io/badge/powered%20by-SQLGlot-orange)](https://github.com/tobymao/sqlglot)

---

## Why SpeakSQL?

Every data team ends up writing the same query five different ways — once per
warehouse. **SpeakSQL** lets you write it once in canonical ANSI SQL and
transpile it into idiomatic SQL for each target:

| Canonical | PostgreSQL | BigQuery | Spark / Databricks |
|---|---|---|---|
| `DATE_TRUNC('month', ts)` | `DATE_TRUNC('MONTH', ts)` | `DATE_TRUNC(ts, MONTH)` | `TRUNC(ts, 'MONTH')` |
| `COUNT(*)` | `COUNT(*)` | `COUNT(*)` | `COUNT(*)` |
| `ROW_NUMBER() OVER (...)` | `... NULLS LAST` | `...` | `...` |

*No more copy-pasting between Postgres and Snowflake. No more debugging
`ILIKE` errors on BigQuery.*

---

## Features

- 🌍 **9 supported dialects** — Postgres, MySQL, T-SQL, Snowflake, BigQuery,
  Spark/Databricks, DuckDB, SQLite, SAP HANA.
- 🧱 **Canonical plan + dialect emission** — built on [SQLGlot](https://github.com/tobymao/sqlglot)
  for battle-tested parsing.
- 🛠️ **Three surfaces** — Python library, CLI, and FastAPI service from one
  install.
- ✅ **30 unit + integration tests** — including real SQLite roundtrips.
- 🎯 **Honest about limits** — HANA has no upstream SQLGlot dialect; we
  route through PostgreSQL emission (HANA is broadly ANSI-compatible) and
  flag known deltas. No black boxes.
- 📜 **Dual-licensed** — AGPL-3.0-or-later for open source, commercial
  license available.

---

## Install

```bash
pip install speaksql
```

**With extras:**

```bash
pip install speaksql[service]   # FastAPI + Uvicorn for the HTTP service
pip install speaksql[dev]       # pytest + ruff + mypy
```

---

## Quick start

### Python library

```python
import speaksql

canonical = (
    "SELECT DATE_TRUNC('month', created_at) AS month, "
    "       country, SUM(amount) AS total "
    "FROM events "
    "GROUP BY month, country "
    "ORDER BY month, total DESC"
)

# One query → many dialects
result = speaksql.transpile(
    canonical,
    targets=("postgres", "bigquery", "snowflake", "tsql", "duckdb"),
)
for dialect, sql in result.items():
    print(f"--- {dialect} ---")
    print(sql)
```

**Sample output (BigQuery):**

```sql
SELECT
  DATE_TRUNC(created_at, MONTH) AS month,
  country,
  SUM(amount) AS total
FROM events
GROUP BY
  month,
  country
ORDER BY
  month,
  total DESC
```

### CLI

```bash
# NL question → all supported dialects
speaksql ask "monthly amount by country"

# Canonical SQL → specific targets only
speaksql ask -d postgres -d snowflake --sql \
  "SELECT id, amount FROM events WHERE amount > 100"

# JSON output (for piping into other tools)
speaksql ask "top 5 country by amount" --json

# List supported dialects
speaksql dialects
```

### HTTP service

```bash
pip install speaksql[service]
uvicorn speaksql.service:app --reload
```

```bash
curl -X POST http://localhost:8000/v1/ask \
  -H 'content-type: application/json' \
  -d '{
        "question": "SELECT DATE_TRUNC('"'"'month'"'"', created_at) AS m, SUM(amount) AS total FROM events GROUP BY m",
        "is_sql": true,
        "dialects": ["postgres", "snowflake", "bigquery"]
      }'
```

```json
{
  "canonical": "SELECT DATE_TRUNC('month', created_at) AS m, SUM(amount) AS total FROM events GROUP BY m",
  "results": {
    "postgres": "SELECT\n  DATE_TRUNC('MONTH', created_at) AS m, ...",
    "snowflake": "SELECT\n  DATE_TRUNC('MONTH', created_at) AS m, ...",
    "bigquery": "SELECT\n  DATE_TRUNC(created_at, MONTH) AS m, ..."
  }
}
```

---

## Supported dialects

| Dialect | Identifier | Aliases | Status |
|---|---|---|---|
| PostgreSQL | `postgres` | — | ✅ native |
| MySQL | `mysql` | — | ✅ native |
| SQL Server | `tsql` | `mssql`, `sqlserver` | ✅ native |
| Snowflake | `snowflake` | — | ✅ native |
| BigQuery | `bigquery` | — | ✅ native |
| Databricks / Spark | `spark` | `databricks` | ✅ native |
| DuckDB | `duckdb` | — | ✅ native |
| SQLite | `sqlite` | — | ✅ native (bundled backend) |
| SAP HANA | `hana` | `saphana` | ⚠️ routed via Postgres — see [HANA caveats](#hana-caveats) |

### HANA caveats

SAP HANA is broadly ANSI-compatible, but SQLGlot does not yet ship a
first-party HANA dialect. SpeakSQL emits through PostgreSQL syntax, which
covers the vast majority of real-world HANA queries. Vendor-specific
functions (`ADD_MONTHS`, `SERIES_GENERATE`, `CEIL`, `MAP`, etc.) need a
hand-written override — currently these are passed through verbatim, so
review them before running.

We're tracking the upstream dialect:
[tobymao/sqlglot — SAP HANA issue](https://github.com/tobymao/sqlglot/issues).
Once a real HANA dialect lands, the `hana → postgres` alias flips with no
breaking change to callers.

---

## Architecture

```
NL question  ─┐
               ▼
    ┌─────────────────────────┐
    │  Schema Discovery        │  per-dialect introspection
    └─────────┬───────────────┘
              ▼
    ┌─────────────────────────┐
    │  Schema Linking          │  NL → {tables, cols, predicates}
    └─────────┬───────────────┘
              ▼
    ┌─────────────────────────┐
    │  Canonical Plan (ANSI)   │  dialect-agnostic SQLGlot AST
    └─────────┬───────────────┘
              ▼
    ┌─────────────────────────┐
    │  Dialect Emitter         │  SQLGlot transpile + override map
    └─────────┬───────────────┘
              ▼
      Postgres | MySQL | T-SQL | Snowflake | BigQuery |
      Spark | DuckDB | SQLite | HANA
```

* The canonical plan is dialect-agnostic — you write ANSI SQL once.
* The override map is narrow: only the things SQLGlot misses (HANA-specific
  functions, T-SQL edge cases). The rest of the work is done by SQLGlot.

---

## Development

```bash
git clone https://github.com/rollroyces/speaksql
cd speaksql
uv sync --all-extras
uv run pytest            # 30 tests
uv run ruff check src tests
uv run python examples/demo_all_dialects.py
```

**Project layout:**

```
speaksql/
├── src/speaksql/
│   ├── core.py          # plan() / emit() / transpile()
│   ├── introspect.py    # schema discovery
│   ├── nl.py            # rule-based NL → canonical SQL
│   ├── cli.py           # `speaksql` command
│   ├── service.py       # FastAPI app (optional)
│   ├── backends/        # dialect-specific DB drivers
│   ├── dialects/        # override modules
│   └── exceptions.py
├── tests/               # pytest, 30 tests across 7 files
├── examples/            # demo scripts
└── .github/workflows/   # CI: Python 3.11, 3.12, 3.13
```

---

## Roadmap

- [ ] Real HANA dialect — submit upstream or vendor locally.
- [ ] Postgres / Snowflake / BigQuery / DuckDB backends (driver extras).
- [ ] LLM-backed NL planner behind a feature flag (currently rule-based).
- [ ] Semantic-diff mode between two dialect emissions.
- [ ] JOIN graph visualizer for the linked schema.

---

## Contributing

Issues and PRs welcome — please open one at
[github.com/rollroyces/speaksql/issues](https://github.com/rollroyces/speaksql/issues).
For significant changes, file an issue first so we can agree on scope.

---

## License

**AGPL-3.0-or-later** — see [LICENSE](LICENSE).

A commercial license is available for teams who want to embed SpeakSQL in
proprietary products without the AGPL obligations.
Contact Royce for terms.

---

<p align="center">
  <sub>Built with SQLGlot · Tested on Python 3.11, 3.12, 3.13 · 30 tests green</sub>
</p>