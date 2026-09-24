# Contributing to SpeakSQL

Thanks for your interest in making SpeakSQL better! This guide is short
on purpose — if you get stuck, the [README](README.md) and the test
suite are the source of truth.

## Quick start

```bash
git clone https://github.com/rollroyces/speaksql
cd speaksql
uv sync --extra dev --extra service --extra duckdb
```

That gets you the CLI, the FastAPI service, the in-process DuckDB
driver (used by many tests), and the dev tooling. Use `uv sync` without
the extras for the base library, or `--all-extras` to pull in **every**
backend driver (postgres, mysql, mssql, snowflake, bigquery, spark — the
last one is heavy, with JVM required).

## Run the test suite

```bash
uv run pytest                # all tests (~281 — 277+1 skip + new ones)
uv run pytest -q             # quieter
uv run pytest tests/test_X.py  # one file
```

The Spark live-JVM test
(`tests/test_spark_backend.py::test_spark_live_local_session_runs_a_query`)
is skipped locally unless `/usr/bin/java` works. CI installs OpenJDK 17
via `actions/setup-java` so it runs there.

## Lint

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

Both must pass before opening a PR. Run `uv run ruff format` to auto-fix
style.

## Project layout

```
src/speaksql/
├── core.py             # transpile() entry point + dialect resolution
├── nl.py               # nl_to_canonical() — NL → canonical SQL
├── llm.py              # LLMProvider Protocol + MockProvider + OpenAI-compat
├── schema_aware.py     # FK-aware planner for natural-language questions
├── planner.py          # Multi-step state-machine planner (advanced mode)
├── examples.py         # Few-shot example library + BM25/Embedding retrievers
├── vendor_overrides.py # Per-dialect function fixes (DuckDB DATEDIFF, etc.)
├── graph.py            # JOIN graph builder + suggest_fks heuristic
├── diff.py             # Semantic SQL diff
├── introspect.py       # Schema introspection across 8 backends
├── cli.py              # Click-based CLI (ask, format, repl, diff, graph)
├── service.py          # FastAPI service (HTTP + WebSocket)
├── exceptions.py       # TranspileError / BackendError / UnsupportedDialectError
├── backends/           # 8 dialect drivers
│   ├── sqlite_backend.py
│   ├── duckdb_backend.py
│   ├── postgres_backend.py
│   ├── mysql_backend.py     # PyMySQL
│   ├── mssql_backend.py      # pymssql
│   ├── snowflake_backend.py  # snowflake-connector-python
│   ├── bigquery_backend.py   # google-cloud-bigquery
│   └── spark_backend.py      # PySpark (JVM required)
└── dialects/
    └── hana.py             # Vendor SAP HANA dialect (subclasses Postgres)
```

## Adding a new dialect

1. **If SQLGlot ships it upstream** — add the dialect name to
   `src/speaksql/core.py` in both `SUPPORTED_DIALECTS` and the
   `DIALECT_ALIASES` map. Write a few transpile tests in
   `tests/test_<name>_dialect.py`.
2. **If SQLGlot doesn't ship it** — subclass an existing dialect in
   `src/speaksql/dialects/<name>.py`, follow the pattern in `hana.py`,
   and add `_register()` that adds to
   `sqlglot.dialects.dialect.Dialect.classes`. The dialect module is
   imported lazily so it costs nothing on the hot path for other
   dialects.

If the new dialect needs function/operator fixes (like our DuckDB
`DATEDIFF` override), use the vendor override registry in
`src/speaksql/vendor_overrides.py` rather than subclassing.

## Adding a new CLI subcommand

CLI commands live in `src/speaksql/cli.py` and use Click. New
subcommands go in the existing `@main.group()` chain — see `repl`,
`format`, `diff`, `graph` for examples. Add a `tests/test_cli_<name>.py`
that drives the CLI via `subprocess.run` so we test the full pipeline
including argparse edge cases.

## Adding a new HTTP endpoint

Endpoints live in `src/speaksql/service.py`. Follow the existing
`@app.post("/v1/<name>")` pattern. Each endpoint should:

1. Accept a Pydantic request model (see `AskRequest`, `DiffRequest`).
2. Return a Pydantic response model OR a `JSONResponse(status_code=400, ...)`
   for explicit error paths.
3. Catch `(TranspileError, BackendError, UnsupportedDialectError)` from
   `speaksql.exceptions` — not bare `Exception`.
4. Be covered by a test in `tests/test_service.py` that uses the
   `TestClient` from FastAPI (the conftest handles deps).

WebSocket endpoints follow a frame protocol: `{"type": "..."}` with
types `llm_token`, `canonical`, `transpile`, `done`, `error`. See
`ws_ask` in `service.py` for the canonical example.

## Adding a test

- Prefer stdlib + small deps. `pytest.importorskip("fastapi")` skips
  cleanly when an extra isn't installed.
- New CLI subcommands: drive via `subprocess.run([sys.executable, "-m",
  "speaksql", "cli", ...])` so we cover the full argv path.
- New HTTP endpoints: use `TestClient(app)` and the `.websocket_connect`
  context manager for WS.
- Live DB backends (Postgres, MySQL, MSSQL, Snowflake, BigQuery): skip
  if the driver isn't installed; CI covers them where creds are available.

## Pull request checklist

- [ ] All tests pass locally (`uv run pytest`)
- [ ] Lint passes (`uv run ruff check src tests scripts`)
- [ ] New code has tests
- [ ] README.md updated if you added a user-facing feature
- [ ] One commit per logical change; squash noise commits
- [ ] Commit message: `<type>(<scope>): <subject>` where type is
      `feat`, `fix`, `test`, `docs`, `refactor`, or `perf`

## Release process

Releases are tag-driven:

1. Bump `version` in `pyproject.toml`
2. `git tag v0.X.Y && git push --tags origin main`
3. The CI matrix verifies green across Python 3.11/3.12/3.13

We do **not** currently publish to PyPI — see the README for how to
install from source. When we're ready to publish, the workflow will be
`uv build && uv publish` with a PyPI token from a maintainer.

## Code of conduct

Be kind. Disagree on substance, not style. Credit other people's work.
The project's license is AGPL-3.0-or-later; contributions are accepted
under the same license.
