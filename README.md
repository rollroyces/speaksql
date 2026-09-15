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

# Transpile AND execute on a live backend
speaksql ask -d duckdb --sql \
  "SELECT SUM(amount) AS total FROM events" \
  --execute-on duckdb \
  --db-path /tmp/events.duckdb \
  --json

# List supported dialects
speaksql dialects
```

### HTTP service

```bash
pip install speaksql[service]
uvicorn speaksql.service:app --reload
```

Transpile only:

```bash
curl -X POST http://localhost:8000/v1/ask \
  -H 'content-type: application/json' \
  -d '{
        "question": "SELECT DATE_TRUNC('"'"'month'"'"', created_at) AS m, SUM(amount) AS total FROM events GROUP BY m",
        "is_sql": true,
        "dialects": ["postgres", "snowflake", "bigquery"]
      }'
```

Transpile **and execute** on a live backend (requires the matching driver extra):

```bash
curl -X POST http://localhost:8000/v1/execute \
  -H 'content-type: application/json' \
  -d '{
        "question": "SELECT DATE_TRUNC('"'"'month'"'"', created_at) AS m, SUM(amount) AS total FROM events GROUP BY m ORDER BY m",
        "is_sql": true,
        "backend": "duckdb",
        "db_path": "/tmp/events.duckdb"
      }'
```

Returns `row_count` + `rows` (capped at 100), with datetime/Decimal values
ISO-formatted for clean JSON parsing.

### Live backends

In addition to transpilation, SpeakSQL ships live-driver backends so you can
verify the emitted SQL against a real engine:

| Dialect | Install | Import |
|---|---|---|
| SQLite | (base install) | `from speaksql.backends import backend_for; backend_for("sqlite")` |
| DuckDB | `pip install speaksql[duckdb]` | `backend_for("duckdb")` |
| PostgreSQL | `pip install speaksql[postgres]` | `backend_for("postgres", host=..., dbname=..., user=..., password=...)` |
| Snowflake | `pip install speaksql[snowflake]` | `backend_for("snowflake", user=..., password=..., account=..., warehouse=...)` |
| BigQuery | `pip install speaksql[bigquery]` | `backend_for("bigquery", project=...)` |
| All four | `pip install speaksql[backends]` | — |

Every backend implements the same `Backend` protocol: `introspect()` returns a
`SchemaList`, `execute(sql)` returns `list[tuple]`, `close()` shuts the
connection. Roundtrip example:

```python
import speaksql
from speaksql.backends import backend_for

be = backend_for("duckdb")  # or "sqlite", "postgres", ...
be.execute("CREATE TABLE events (id INT, amount DOUBLE)")
be.execute("INSERT INTO events VALUES (1, 100.0), (2, 200.0)")

canonical = "SELECT SUM(amount) AS total FROM events"
results = speaksql.transpile(canonical, targets=("duckdb",))
print(be.execute(results["duckdb"]))
# [(300.0,)]

schema = be.introspect()
for t in schema.tables:
    print(f"{t.schema}.{t.name}: {[c.name for c in t.columns]}")
# main.events: ['id', 'amount']
```

### LLM planner

The default NL→SQL planner in `speaksql.nl` is rule-based — three regex
patterns covering `count of X`, `top N X by Y`, and `monthly X by Y`. When
a query doesn't match, it returns a syntactically valid placeholder SQL
with the original question preserved as a comment. **No fabrication.**

For more flexible NL handling, set `SPEAKSQL_USE_LLM=1` and configure
a provider. SpeakSQL ships:

- `MockProvider` — returns canned canonical SQL for testing. The default.
- `OpenAICompatibleProvider` — POST to any OpenAI-shaped endpoint via
  stdlib `urllib`. No SDK required. Works with OpenAI, Together, Groq,
  OpenRouter, vLLM, Ollama, and local llama.cpp.

Env vars:

```bash
export SPEAKSQL_USE_LLM=1
export SPEAKSQL_LLM_BASE_URL=https://api.openai.com/v1
export SPEAKSQL_LLM_API_KEY=sk-...              # or OPENAI_API_KEY
export SPEAKSQL_LLM_MODEL=gpt-4o-mini
export SPEAKSQL_LLM_AUTH_HEADER=Authorization  # or x-api-key for Anthropic-style
export SPEAKSQL_LLM_AUTH_PREFIX='Bearer '
export SPEAKSQL_LLM_TIMEOUT_S=30
export SPEAKSQL_LLM_MAX_TOKENS=512
export SPEAKSQL_LLM_TEMPERATURE=0.0
```

Programmatic:

```python
import speaksql
from speaksql.llm import MockProvider, OpenAICompatibleProvider

# Mock (default; no network)
p = MockProvider()
sql = speaksql.llm_to_canonical("show all users", provider=p)

# Custom OpenAI-compatible endpoint
p = OpenAICompatibleProvider(
    base_url="https://api.together.xyz/v1",
    model="meta-llama/Llama-3-70b-chat-hf",
    api_key="...",
)
sql = speaksql.llm_to_canonical("monthly active users", provider=p)
```

An eval harness lives at `examples/llm_eval/run.py`. Run with the mock
provider to verify your prompt changes don't regress rule-based
behavior:

```bash
PYTHONPATH=src python examples/llm_eval/run.py
# 7/7 cases passed.
```

Pass `--provider openai` (with `SPEAKSQL_LLM_BASE_URL` set) to evaluate
against a real LLM.

### JOIN graph

Given a schema (from `Backend.introspect()`), SpeakSQL builds a JOIN
graph by **preferring real FK constraints** and falling back to a name
heuristic. The two layers:

1. **Real FK constraints (preferred):** SQLite `PRAGMA foreign_key_list`,
   Postgres `information_schema.referential_constraints` joined with
   `key_column_usage`, DuckDB `duckdb_constraints()`, Snowflake
   `information_schema.referential_constraints` (informational only —
   often empty). BigQuery has no FK concept and returns `()`.
2. **Name-based heuristic (fallback):** when no FKs are available, we
   look for `{singular(other)}_id` matching the other table's PK. PK↔PK
   same-name noise is filtered out. Type mismatches disqualify.

CLI:

```bash
speaksql graph ./analytics.sqlite --backend sqlite --html ./graph.html
# 4 tables · 2 inferred joins (2 from declared FKs)
#   order_items <-> orders  via order_id
#   orders <-> users  via user_id
```

The HTML is self-contained — no JS, no external CSS, no CDN — and
embeds an SVG diagram plus a column listing. Service:

```bash
curl -X POST http://localhost:8000/v1/graph \
  -H 'content-type: application/json' \
  -d '{"db_path": "./analytics.sqlite", "backend": "sqlite"}'
# Returns {db_path, backend, nodes, graph, html}
```

Programmatic:

```python
import speaksql
from speaksql.backends import backend_for

be = backend_for("sqlite", path="./analytics.sqlite")
schema = be.introspect()
fks = be.foreign_keys()  # declared FK constraints
be.close()

g = speaksql.build_join_graph(schema.with_foreign_keys(fks))
# pass use_real_fks=False to force the name heuristic
print(g.to_dict())
```

### Semantic diff

Compare two SQL strings (possibly from different dialects) and see the
**semantic** differences — not just textual ones:

```python
import speaksql

# Same canonical query, transpiled to Postgres and BigQuery
postgres = "SELECT id FROM t ORDER BY id ASC NULLS LAST"
bigquery = "SELECT id FROM t ORDER BY id ASC"

diffs = speaksql.semantic_diff(postgres, bigquery, dialect_a="postgres", dialect_b="bigquery")
print(speaksql.format_diff(diffs))
```

Output:

```
1 semantic difference(s):
  1. [null_ordering] @ ORDER BY #0
     a: 'NULLS LAST'
     b: 'NULLS FIRST'  (null ordering differs)
```

Detected categories: `function_call` (same function, different call form),
`null_ordering` (NULLS FIRST/LAST), `distinct_syntax` (TOP vs LIMIT,
ASC vs DESC), `type_name` (DOUBLE PRECISION vs FLOAT8), `literal`
(quoting), `predicate`, `structural` (different tables/projections).

CLI:

```bash
speaksql diff "SELECT id FROM t ORDER BY id ASC NULLS LAST" \
            "SELECT id FROM t ORDER BY id ASC" --json
```

Service:

```bash
curl -X POST http://localhost:8000/v1/diff \
  -H 'content-type: application/json' \
  -d '{"sql_a": "SELECT id FROM t ORDER BY id ASC NULLS LAST",
       "sql_b": "SELECT id FROM t ORDER BY id ASC"}'
# {"identical": false, "differences": [{"category": "null_ordering", ...}]}
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
| SAP HANA | `hana` | `saphana` | ✅ vendor (see [HANA dialect](#sap-hana-hana)) |

### SAP HANA (`hana`)

SpeakSQL ships a vendor-side HANA dialect (`src/speaksql/dialects/hana.py`)
that subclasses SQLGlot's Postgres parser. Upstream SQLGlot does not yet
ship a HANA dialect, so we maintain one locally.

The HANA dialect:

- Inherits Postgres grammar (HANA is broadly ANSI-compatible)
- Removes Postgres's 2-arity `MAP` binding — HANA's `MAP(k1, v1, k2, v2, ...)`
  takes variable even-arity pairs
- Roundtrips HANA-specific functions through unchanged: `ADD_MONTHS`,
  `DAYS_BETWEEN`, `SECONDS_BETWEEN`, `SERIES_GENERATE_DATE`,
  `BINNING`, `TO_VARCHAR`, `IFNULL`, etc.

When upstream SQLGlot ships a real HANA dialect, this module either
inherits from it or becomes a thin shim — callers don't notice.

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

- [x] ~~Real HANA dialect~~ — shipped via `src/speaksql/dialects/hana.py`
      (vendor dialect subclassing Postgres)
- [x] ~~Postgres / Snowflake / BigQuery / DuckDB backends~~ — shipped
      via per-dialect extras
- [x] ~~LLM-backed NL planner behind a feature flag~~ — shipped via
      `src/speaksql/llm.py` (`SPEAKSQL_USE_LLM=1`)
- [x] ~~Semantic diff mode between two dialect emissions~~ — shipped
      via `src/speaksql/diff.py` (`speaksql diff`, `/v1/diff`)
- [x] ~~JOIN graph visualizer for the linked schema~~ — shipped via
      `src/speaksql/graph.py` (`speaksql graph`, `/v1/graph`)

All roadmap items from the original v0.1 launch are shipped. New ideas:

- [x] ~~Read real FK constraints from `information_schema.referential_constraints`
      instead of name heuristics~~ — shipped; SQLite `PRAGMA`, Postgres
      `information_schema`, DuckDB `duckdb_constraints()`, Snowflake
      `information_schema`, BigQuery (no-op)
- [ ] Custom Snowflake / BigQuery / HANA vendor overrides (functions,
      types, syntactic idioms)
- [ ] Streaming LLM provider for long SQL generation
- [ ] WebSocket /v1/ask endpoint with partial-response streaming

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