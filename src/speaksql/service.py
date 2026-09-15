"""Optional FastAPI service.

Importing this module requires `fastapi` and `uvicorn` — install via:

    pip install speaksql[service]

Run with:

    uvicorn speaksql.service:app --reload
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from speaksql import SUPPORTED_DIALECTS, transpile
from speaksql.exceptions import BackendError, SpeakSQLError
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


@app.get("/v1/dialects")
def dialects() -> dict[str, list[str]]:
    return {"supported": sorted(SUPPORTED_DIALECTS)}