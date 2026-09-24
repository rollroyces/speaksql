"""Integration test for examples/ws_client.py against the real service.

Drives the WS example client over FastAPI's TestClient.websocket_connect
against the actual /v1/ask endpoint. Skips if [service] extra not
installed (TestClient import fails).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient
from speaksql.service import app

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load_ws_client():
    """Load examples/ws_client.py as a module without it being on sys.path."""
    spec = importlib.util.spec_from_file_location(
        "ws_client", EXAMPLES_DIR / "ws_client.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ws_client():
    return _load_ws_client()


@pytest.fixture
def service_client():
    with TestClient(app) as client:
        yield client


def _drain(service_client, payload: dict) -> list[dict]:
    """Send `payload` over /v1/ask and return every frame until done/error."""
    frames: list[dict] = []
    with service_client.websocket_connect("/v1/ask") as ws:
        ws.send_json(payload)
        # Cap at 50 frames so a stuck server doesn't hang the test.
        for _ in range(50):
            msg = ws.receive_json()
            frames.append(msg)
            if msg.get("type") in ("done", "error"):
                break
    return frames


def test_ws_client_imports_cleanly(ws_client):
    """The example module exposes stream() and main()."""
    assert callable(ws_client.stream)
    assert callable(ws_client.main)


def test_ws_client_stream_produces_done_frame(ws_client, service_client):
    """A canonical-SQL question produces canonical + transpile + done."""
    frames = _drain(
        service_client,
        {
            "question": "SELECT 1 AS x",
            "dialects": ["postgres", "duckdb"],
            "is_sql": True,
        },
    )
    kinds = [f.get("type") for f in frames]
    assert "canonical" in kinds, f"missing canonical frame: {kinds}"
    assert kinds.count("transpile") == 2  # one per dialect
    assert kinds[-1] == "done"


def test_ws_client_rejects_empty_question(ws_client, service_client):
    """Empty question yields an error frame, not a silent success."""
    frames = _drain(service_client, {"question": "", "dialects": ["postgres"]})
    assert len(frames) == 1
    assert frames[0].get("type") == "error"
    assert "empty" in frames[0].get("message", "").lower()


def test_ws_client_rejects_unknown_dialect(ws_client, service_client):
    """Unknown dialect yields an error frame that names the dialect."""
    frames = _drain(
        service_client,
        {"question": "SELECT 1", "dialects": ["postgres", "oracle"]},
    )
    assert len(frames) == 1
    assert frames[0].get("type") == "error"
    assert "oracle" in frames[0].get("message", "")
