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
- 🔌 **Live backends** — actually execute transpiled SQL against SQLite,
  DuckDB, Postgres, Snowflake, or BigQuery. Real FK constraints are
  read from the database and used to build JOIN graphs.
- 🧠 **LLM planner** — rule-based by default; opt-in via
  `SPEAKSQL_USE_LLM=1` for any OpenAI-compatible endpoint (no SDK).
- 🔍 **Semantic diff** — compare two SQL strings and see *why* they're
  different (NULLS FIRST/LAST, ASC/DESC, TOP/LIMIT, function-call rewrites,
  type aliases, structural). Not just textual.
- 🗺️ **JOIN graph visualizer** — self-contained HTML diagrams from schema
  + FK metadata.
- ✅ **121 tests passing** — unit + integration, including live SQLite
  and DuckDB roundtrips.
- 🎯 **Honest about limits** — vendor dialect for HANA (upstream SQLGlot
  lacks one); BigQuery has no FK concept; no fabrication in NL→SQL.
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
pip install speaksql[postgres]  # psycopg driver
pip install speaksql[duckdb]    # in-process DuckDB driver (great for tests)
pip install speaksql[snowflake] # snowflake-connector-python
pip install speaksql[bigquery]  # google-cloud-bigquery
pip install speaksql[backends]  # all four of the above
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

# Semantic diff between two SQL strings
speaksql diff "SELECT id FROM t ORDER BY id ASC NULLS LAST" \
            "SELECT id FROM t ORDER BY id ASC" --json

# JOIN graph from a SQLite database
speaksql graph ./analytics.sqlite --backend sqlite --html ./graph.html

# List supported dialects
speaksql dialects
```

| Subcommand | Purpose |
|---|---|
| `ask` | Transpile NL or canonical SQL to one or more dialects; optionally execute |
| `diff` | Compare two SQL strings semantically (NULLS, ASC/DESC, function rewrites, etc.) |
| `graph` | Build a JOIN graph from a SQLite/DuckDB file; emit JSON, text, or HTML |
| `dialects` | Print all supported dialect identifiers |

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

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/ask` | POST | Transpile SQL or NL to one or more dialects |
| `/v1/execute` | POST | Transpile + execute on a live backend (sqlite/duckdb/postgres/snowflake/bigquery) |
| `/v1/diff` | POST | Compare two SQL strings semantically; returns `{identical, differences}` |
| `/v1/graph` | POST | Build a JOIN graph from a SQLite/DuckDB file; returns JSON + HTML |
| `/v1/dialects` | GET | List supported dialect identifiers |

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

| Dialect | Identifier | Aliases | Status | Live FK introspection |
|---|---|---|---|---|
| PostgreSQL | `postgres` | — | ✅ native + backend | ✅ `information_schema.referential_constraints` |
| MySQL | `mysql` | — | ✅ native (transpile only) | n/a (no MySQL driver shipped) |
| SQL Server | `tsql` | `mssql`, `sqlserver` | ✅ native (transpile only) | n/a (no SQL Server driver shipped) |
| Snowflake | `snowflake` | — | ✅ native + backend | ✅ informational only — often empty |
| BigQuery | `bigquery` | — | ✅ native + backend | ❌ no FK concept in BigQuery DDL |
| Databricks / Spark | `spark` | `databricks` | ✅ native (transpile only) | n/a (no Spark driver shipped) |
| DuckDB | `duckdb` | — | ✅ native + backend | ✅ `duckdb_constraints()` |
| SQLite | `sqlite` | — | ✅ native + backend (bundled) | ✅ `PRAGMA foreign_key_list` |
| SAP HANA | `hana` | `saphana` | ✅ vendor (see [HANA dialect](#sap-hana-hana)) | ❌ not yet exposed |

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
              │
canonical SQL ─┤
              ▼
    ┌─────────────────────────┐
    │  Schema Discovery        │  per-dialect introspection
    │   ├─ tables + columns    │  SQLite PRAGMA, DuckDB
    │   └─ FK constraints      │  information_schema (Postgres, Snowflake),
    └─────────┬───────────────┘  duckdb_constraints() (DuckDB), PRAGMA (SQLite)
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
    │  Dialect Emitter         │  SQLGlot transpile + vendor dialects
    │   └─ Hana dialect        │  local Postgres subclass
    └─────────┬───────────────┘
              ▼
      Postgres | MySQL | T-SQL | Snowflake | BigQuery |
      Spark | DuckDB | SQLite | HANA

Plus three orthogonal tools:

  • LLM planner  — replaces the rule-based NL layer when
    SPEAKSQL_USE_LLM=1. Stdlib urllib, no SDK, OpenAI-compatible.
  • Semantic diff — compares two SQL strings AST-aware.
  • JOIN graph   — uses real FKs first, name heuristic second.
    Self-contained HTML output.
```

* The canonical plan is dialect-agnostic — you write ANSI SQL once.
* The vendor dialects are narrow: only the things SQLGlot misses (HANA's
  `MAP` arity, T-SQL edge cases, BigQuery FK-less introspection). The
  rest of the work is done by SQLGlot.

---

## Development

```bash
git clone https://github.com/rollroyces/speaksql
cd speaksql
uv sync --all-extras
uv run pytest            # 121 tests
uv run ruff check src tests
uv run python examples/demo_all_dialects.py
PYTHONPATH=src python examples/llm_eval/run.py   # 7/7 cases
```

**Project layout:**

```
speaksql/
├── src/speaksql/
│   ├── core.py                # plan() / emit() / transpile()
│   ├── introspect.py          # SchemaList + ForeignKeyInfo
│   ├── nl.py                  # rule-based NL → canonical SQL (with LLM fallback)
│   ├── llm.py                 # LLMProvider, MockProvider, OpenAICompatibleProvider
│   ├── diff.py                # semantic_diff + DiffEntry
│   ├── graph.py               # JOIN graph builder + HTML renderer
│   ├── cli.py                 # `speaksql` command (ask, diff, graph, dialects)
│   ├── service.py             # FastAPI app (optional)
│   ├── exceptions.py
│   ├── backends/              # per-dialect DB drivers (SQLite, DuckDB,
│   │                          #   Postgres, Snowflake, BigQuery)
│   └── dialects/              # vendor dialects (hana.py)
├── tests/                     # 121 tests across 19 files
├── examples/
│   ├── demo_all_dialects.py
│   └── llm_eval/              # eval harness + eval_set.jsonl
└── .github/workflows/         # CI: Python 3.11, 3.12, 3.13
```

---

## Roadmap

All five items from the original v0.1 roadmap are shipped:

- [x] ~~Real HANA dialect~~ — shipped via `src/speaksql/dialects/hana.py`
      (vendor dialect subclassing Postgres)
- [x] ~~Postgres / Snowflake / BigQuery / DuckDB backends~~ — shipped
      via per-dialect extras (`[postgres]`, `[duckdb]`, `[snowflake]`,
      `[bigquery]`, `[backends]`)
- [x] ~~LLM-backed NL planner behind a feature flag~~ — shipped via
      `src/speaksql/llm.py` (`SPEAKSQL_USE_LLM=1`)
- [x] ~~Semantic diff mode between two dialect emissions~~ — shipped
      via `src/speaksql/diff.py` (`speaksql diff`, `/v1/diff`)
- [x] ~~JOIN graph visualizer for the linked schema~~ — shipped via
      `src/speaksql/graph.py` (`speaksql graph`, `/v1/graph`)
- [x] ~~Read real FK constraints from `information_schema.referential_constraints`
      instead of name heuristics~~ — shipped; SQLite `PRAGMA`, Postgres
      `information_schema`, DuckDB `duckdb_constraints()`, Snowflake
      `information_schema`, BigQuery (no-op)

New ideas being considered:

- [ ] Custom Snowflake / BigQuery / HANA vendor overrides (functions,
      types, syntactic idioms)
- [ ] MySQL and SQL Server backends (currently transpile-only)
- [ ] Streaming LLM provider for long SQL generation
- [ ] WebSocket `/v1/ask` endpoint with partial-response streaming

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
  <sub>Built with SQLGlot · Tested on Python 3.11, 3.12, 3.13 · 121 tests green</sub>
</p>