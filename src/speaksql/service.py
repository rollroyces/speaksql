"""Optional FastAPI service.

Importing this module requires `fastapi` and `uvicorn` — install via:

    pip install speaksql[service]

Run with:

    uvicorn speaksql.service:app --reload
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import speaksql.llm
from speaksql import SUPPORTED_DIALECTS, __version__, semantic_diff, transpile
from speaksql.exceptions import (
    BackendError,
    SpeakSQLError,
    TranspileError,
    UnsupportedDialectError,
)
from speaksql.nl import nl_to_canonical

# Backends that can be used as execute targets. Mirrors the CLI choice set.
EXECUTE_BACKENDS = ("sqlite", "duckdb", "postgres", "snowflake", "bigquery")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    dialects: list[str] | None = Field(
        default=None,
        description="Target dialects; default = all supported.",
    )
    is_sql: bool = Field(
        default=False,
        description="If true, treat `question` as canonical ANSI SQL.",
    )


class AskResponse(BaseModel):
    canonical: str
    results: dict[str, str]


class ExecuteRequest(BaseModel):
    question: str = Field(..., min_length=1)
    backend: str = Field(..., description=f"One of {list(EXECUTE_BACKENDS)}")
    is_sql: bool = Field(default=False)
    db_path: str | None = Field(
        default=None,
        description="For sqlite/duckdb, point at this file instead of :memory:.",
    )


class ExecuteResponse(BaseModel):
    canonical: str
    backend: str
    executed: bool
    row_count: int | None = None
    rows: list[list] | None = None
    error: str | None = None
    reason: str | None = None


class DiffRequest(BaseModel):
    sql_a: str = Field(..., min_length=1)
    sql_b: str = Field(..., min_length=1)
    dialect_a: str | None = Field(default=None)
    dialect_b: str | None = Field(default=None)


class DiffResponse(BaseModel):
    identical: bool
    differences: list[dict[str, str | None]]


class GraphRequest(BaseModel):
    db_path: str = Field(..., min_length=1, description="Path to a SQLite (or DuckDB) database file.")
    backend: str = Field(default="sqlite", description="'sqlite' or 'duckdb'.")
    title: str | None = Field(default=None)


class GraphResponse(BaseModel):
    db_path: str
    backend: str
    nodes: int
    edges: int
    graph: dict[str, object]
    suggested_fks: list[dict[str, object]] = Field(
        default_factory=list,
        description=(
            "Heuristic FK candidates the user did not declare. Each item has "
            "from_table/from_column/to_table/to_column/confidence/reason."
        ),
    )
    html: str


app = FastAPI(
    title="SpeakSQL",
    description="Speak once. Query any dialect.",
    version="0.1.0",
)


@app.post("/v1/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    targets: Iterable[str] = req.dialects or sorted(SUPPORTED_DIALECTS)
    for d in targets:
        if d not in SUPPORTED_DIALECTS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported dialect '{d}'. Supported: {sorted(SUPPORTED_DIALECTS)}",
            )
    canonical = req.question if req.is_sql else nl_to_canonical(req.question)
    try:
        results = transpile(canonical, targets)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return AskResponse(canonical=canonical, results=results)


@app.post("/v1/execute", response_model=ExecuteResponse)
def execute(req: ExecuteRequest) -> ExecuteResponse:
    """Transpile + execute on a single backend.

    The chosen backend's natural dialect must match the target — passing
    backend='duckdb' against transpiled postgres SQL would silently fail.
    We use the backend's name as the target dialect, so callers should
    pass canonical SQL matching that dialect.
    """
    if req.backend not in EXECUTE_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported execute backend '{req.backend}'. "
            f"Choose from: {list(EXECUTE_BACKENDS)}",
        )

    canonical = req.question if req.is_sql else nl_to_canonical(req.question)
    try:
        results = transpile(canonical, (req.backend,))
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    sql_to_run = results[req.backend]

    from speaksql.backends import backend_for

    kwargs: dict[str, object] = {}
    if req.db_path is not None and req.backend in ("sqlite", "duckdb"):
        kwargs["path"] = req.db_path
    try:
        be = backend_for(req.backend, **kwargs)
    except BackendError as e:
        return ExecuteResponse(
            canonical=canonical, backend=req.backend, executed=False, reason=str(e)
        )
    except Exception as e:  # noqa: BLE001
        # Driver may be installed but refuse to construct without credentials
        # (e.g. snowflake-connector-python reads its config manager on
        # instantiation). Surface that as a friendly reason, not a 500.
        return ExecuteResponse(
            canonical=canonical,
            backend=req.backend,
            executed=False,
            reason=f"backend construction failed: {type(e).__name__}: {e}",
        )

    try:
        rows = be.execute(sql_to_run)
        # JSON-friendly serialization: ISO format for datetime/date, str fallback
        serialised = [[_jsonify(v) for v in row] for row in rows[:100]]
        return ExecuteResponse(
            canonical=canonical,
            backend=req.backend,
            executed=True,
            row_count=len(rows),
            rows=serialised,
        )
    except SpeakSQLError as e:
        return ExecuteResponse(
            canonical=canonical, backend=req.backend, executed=False, error=str(e)
        )
    finally:
        be.close()


def _jsonify(v: object) -> object:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "as_tuple"):  # Decimal
        return float(v)
    return v


@app.post("/v1/diff", response_model=DiffResponse)
def diff(req: DiffRequest) -> DiffResponse:
    """Compare two SQL strings semantically.

    Returns `identical: true` and an empty `differences` list when the two
    SQL strings parse to semantically equivalent ASTs. Otherwise returns the
    categorised differences (function-call rewrites, null-ordering, etc.).
    """
    try:
        diffs = semantic_diff(
            req.sql_a,
            req.sql_b,
            dialect_a=req.dialect_a or "",
            dialect_b=req.dialect_b or "",
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return DiffResponse(
        identical=len(diffs) == 0,
        differences=[d.to_dict() for d in diffs],
    )


@app.post("/v1/graph", response_model=GraphResponse)
def graph(req: GraphRequest) -> GraphResponse:
    """Build a JOIN graph from a SQLite/DuckDB database file.

    Returns the graph structure as JSON plus a self-contained HTML
    visualization that can be saved and viewed offline.
    """
    if req.backend not in ("sqlite", "duckdb"):
        raise HTTPException(
            status_code=400,
            detail=f"backend '{req.backend}' not supported for /v1/graph (use sqlite or duckdb)",
        )

    from speaksql.backends import backend_for
    from speaksql.graph import build_join_graph, render_html

    try:
        be = backend_for(req.backend, path=req.db_path)
    except BackendError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        schema = be.introspect()
        fks = be.foreign_keys()
    finally:
        be.close()

    enriched = schema.with_foreign_keys(fks)
    g = build_join_graph(enriched)
    title = req.title or f"JOIN Graph — {req.db_path}"
    # Heuristic FK suggestions the user didn't declare. Useful for users
    # who don't have foreign-key constraints set up — they get a list of
    # likely-FKs to consider adding.
    from speaksql.graph import suggest_fks

    suggestions = suggest_fks(enriched)
    suggested_dicts = [
        {
            "from_table": s.from_table,
            "from_column": s.from_column,
            "to_table": s.to_table,
            "to_column": s.to_column,
            "confidence": s.confidence,
            "reason": s.reason,
        }
        for s in suggestions
    ]
    return GraphResponse(
        db_path=req.db_path,
        backend=req.backend,
        nodes=len(g.nodes),
        edges=len(g.edges),
        graph=g.to_dict(),
        html=render_html(g, title=title),
        suggested_fks=suggested_dicts,
    )


@app.get("/v1/dialects")
def dialects() -> dict[str, list[str]]:
    return {"supported": sorted(SUPPORTED_DIALECTS)}


@app.get("/v1/health")
def health() -> dict[str, str]:
    """Liveness check.

    Returns 200 OK with the service version. Use this for k8s/load-balancer
    health probes — it doesn't do any work beyond the framework default
    route resolution.
    """
    return {
        "status": "ok",
        "version": __version__,
        "service": "speaksql",
    }


@app.get("/v1/schema", response_model=None)
def get_schema(
    db: str = "sqlite",
    db_path: str | None = None,
):
    """Introspect a database and return the schema as JSON.

    Query params:
        db: backend key (sqlite | duckdb | postgres | mysql | mssql | snowflake
            | bigquery | spark | hana). Default sqlite.
        db_path: for sqlite/duckdb, the file path to open. For server-side
            backends, leave db_path empty — the BACKEND connects via its
            own env config. Returns 503 if the chosen backend can't be
            reached.
    """
    from speaksql.backends import backend_for

    try:
        if db in ("sqlite", "duckdb"):
            if not db_path:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "missing db_path",
                        "hint": f"for {db}, pass ?db_path=/path/to/file.db",
                    },
                )
            backend = backend_for(db, path=db_path)
        else:
            backend = backend_for(db)
    except UnsupportedDialectError as exc:
        return JSONResponse(
            status_code=503,
            content={"error": str(exc), "backend": db},
        )
    except BackendError as exc:
        return JSONResponse(
            status_code=503,
            content={"error": str(exc), "backend": db},
        )
    except (ImportError, ModuleNotFoundError) as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": f"backend '{db}' requires an optional driver: {exc}",
                "backend": db,
            },
        )

    try:
        schema = backend.introspect()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return JSONResponse(
            status_code=503,
            content={"error": f"introspect failed: {exc}", "backend": db},
        )

    return {
        "backend": db,
        "tables": [_table_to_dict(t) for t in schema.tables],
        "foreign_keys": [_fk_to_dict(fk) for fk in schema.foreign_keys],
    }


def _table_to_dict(t) -> dict:
    return {
        "schema": getattr(t, "schema", None),
        "name": t.name,
        "columns": [
            {
                "name": c.name,
                "type": c.data_type,
                "nullable": c.nullable,
                "primary_key": c.is_primary_key,
            }
            for c in t.columns
        ],
    }


def _fk_to_dict(fk) -> dict:
    return {
        "from_table": fk.from_table,
        "from_column": fk.from_column,
        "to_table": fk.to_table,
        "to_column": fk.to_column,
    }


# ---------------------------------------------------------------------------
# WebSocket /v1/ask — streaming variant of the POST /v1/ask endpoint.
#
# Protocol:
#   client → server: {"question": str, "dialects": [str], "is_sql": bool}
#   server → client (one frame each):
#       {"type": "canonical", "sql": str}              # canonical SQL we used
#       {"type": "transpile", "dialect": str, "sql": str}   # 1 per target
#       {"type": "diff", "dialect": str, "diffs": [...]}      # if asked
#       {"type": "done"}
#       {"type": "error", "message": str}
#
# If the LLM planner is enabled (SPEAKSQL_USE_LLM=1), canonical SQL is
# streamed token-by-token from the upstream endpoint; the assembled
# canonical SQL is then transpiled per-target before the per-dialect
# frames are sent. This avoids holding the whole response before the
# client sees anything.
# ---------------------------------------------------------------------------


@app.websocket("/v1/ask")
async def ws_ask(ws: WebSocket) -> None:
    await ws.accept()
    try:
        payload = await ws.receive_json()
    except (ValueError, RuntimeError) as e:  # pragma: no cover — malformed payload
        await ws.send_json({"type": "error", "message": f"bad payload: {e}"})
        await ws.close()
        return

    question = str(payload.get("question", "")).strip()
    if not question:
        await ws.send_json({"type": "error", "message": "empty question"})
        await ws.close()
        return

    is_sql = bool(payload.get("is_sql", False))
    targets = payload.get("dialects") or sorted(SUPPORTED_DIALECTS)
    for d in targets:
        if d not in SUPPORTED_DIALECTS:
            await ws.send_json(
                {"type": "error", "message": f"unsupported dialect '{d}'"}
            )
            await ws.close()
            return

    # Resolve canonical SQL, streaming from the LLM if appropriate.
    canonical_sql = await _resolve_canonical_ws(
        ws, question, is_sql=is_sql
    )
    if canonical_sql is None:
        # An error frame was already sent; close out.
        return
    await ws.send_json({"type": "canonical", "sql": canonical_sql})

    # Per-dialect transpilation. Each is small enough to emit in one frame.
    from speaksql import transpile

    try:
        results = transpile(canonical_sql, targets)
    except (TranspileError, ValueError, TypeError) as e:
        await ws.send_json({"type": "error", "message": f"transpile failed: {e}"})
        await ws.close()
        return
    for dialect, sql in results.items():
        await ws.send_json({"type": "transpile", "dialect": dialect, "sql": sql})

    await ws.send_json({"type": "done"})


async def _resolve_canonical_ws(
    ws: WebSocket, question: str, *, is_sql: bool
) -> str | None:
    """Decide the canonical SQL; stream from LLM if appropriate.

    Returns the assembled canonical SQL string, or None if an error
    frame was already sent to the client.

    Decision tree:
        is_sql → echo the question back as-is.
        otherwise → if SPEAKSQL_USE_LLM=1, bypass the rule-based
        `nl_to_canonical` (which would silently call the mock LLM
        internally) and stream directly. Otherwise, run the rule layer
        and use its result.
    """
    if is_sql:
        return question

    # Late-import so test-time monkey-patching of `llm.make_provider` is
    # effective.
    from speaksql import llm as _llm
    from speaksql.nl import nl_to_canonical

    if not _llm.is_llm_enabled():
        # Pure rule-based path — fast, no LLM round-trip.
        return nl_to_canonical(question)

    # LLM streaming path. Note we deliberately do NOT call
    # nl_to_canonical here — it would invoke the LLM itself and we'd
    # lose the streaming signal. Instead, run the rule patterns
    # inline and stream only when they miss.
    rule_result = _apply_rule_patterns(question)
    if not rule_result.lstrip().startswith("-- could not parse"):
        return rule_result

    try:
        chunks: list[str] = []
        # Use the async streaming variant so blocking HTTP reads happen
        # on a thread executor and don't stall the event loop. The
        # previous approach was to iterate the sync generator with
        # ``await asyncio.sleep(0.01)`` between chunks, which both
        # adds latency and ties up the loop. The async variant lets
        # the loop schedule other WebSocket connections in parallel.
        async for chunk in _llm.llm_to_canonical_streaming_async(question):
            chunks.append(chunk)
            await ws.send_json({"type": "llm_token", "delta": chunk})
    except (speaksql.llm.LLMError, OSError, ValueError, TypeError) as e:
        await ws.send_json({"type": "error", "message": f"LLM stream failed: {e}"})
        return None

    assembled = "".join(chunks).strip()
    # Fall back to the rule-layer placeholder if the LLM gave us nothing.
    if not assembled:
        return rule_result
    return assembled


def _apply_rule_patterns(question: str) -> str:
    """Run the rule-based NL patterns and return canonical SQL or placeholder.

    Mirrors `speaksql.nl.nl_to_canonical` minus the LLM fallback —
    we don't want a side-channel LLM call here.
    """

    from speaksql.nl import _PATTERNS

    q = question.strip().rstrip("?.!")
    for pat, tmpl in _PATTERNS:
        m = pat.match(q)
        if m:
            try:
                return tmpl.format(**m.groupdict())
            except KeyError:
                continue
    return f"-- could not parse: {question}\nSELECT 1 AS placeholder"