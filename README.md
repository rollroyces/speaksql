# SpeakSQL

> **Speak once. Query any dialect.**

A dialect-aware SQL transpiler. Build a canonical ANSI query plan once,
emit correct syntax for every supported dialect.

## Supported dialects

- PostgreSQL
- MySQL
- SQL Server (T-SQL)
- Snowflake
- BigQuery
- Databricks / Spark
- DuckDB
- SAP HANA

## Install

```bash
pip install speaksql
```

With extras:

```bash
pip install speaksql[service]   # FastAPI + Uvicorn for the HTTP service
pip install speaksql[dev]       # pytest, ruff, mypy
```

## Library

```python
import speaksql

# Translate one canonical SQL to many dialects
result = speaksql.transpile(
    "SELECT DATE_TRUNC('month', created_at) AS m, SUM(amount) AS total "
    "FROM events GROUP BY m ORDER BY m",
    targets=("postgres", "mysql", "snowflake", "tsql"),
)
for dialect, sql in result.items():
    print(f"--- {dialect} ---")
    print(sql)
```

## CLI

```bash
# NL question → all dialects
speaksql ask "monthly amount by country"

# Canonical SQL → only Postgres and Snowflake
speaksql ask -d postgres -d snowflake --sql \
  "SELECT id, amount FROM events WHERE amount > 100"

# JSON output
speaksql ask "top 5 country by amount" --json

# List supported dialects
speaksql dialects
```

## Service

```bash
pip install speaksql[service]
uvicorn speaksql.service:app --reload
```

```bash
curl -X POST http://localhost:8000/v1/ask \
  -H 'content-type: application/json' \
  -d '{"question": "monthly amount by country"}'
```

## License

AGPL-3.0-or-later. Commercial licensing available on request.