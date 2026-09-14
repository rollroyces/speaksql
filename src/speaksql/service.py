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
from speaksql.nl import nl_to_canonical


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


@app.get("/v1/dialects")
def dialects() -> dict[str, list[str]]:
    return {"supported": sorted(SUPPORTED_DIALECTS)}