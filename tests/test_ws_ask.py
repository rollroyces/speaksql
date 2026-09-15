"""WebSocket /v1/ask endpoint tests.

Covers the basic protocol frames (canonical, transpile, done, error) plus
the LLM streaming path with a chunked mock provider.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.service


def test_ws_ask_with_canonical_sql():
    """NL-style payload with is_sql=True passes through."""
    from fastapi.testclient import TestClient
    from speaksql.service import app
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client, client.websocket_connect("/v1/ask") as ws:
        ws.send_json(
            {
                "question": "SELECT 1 AS x",
                "is_sql": True,
                "dialects": ["postgres", "duckdb"],
            }
        )
        frames: list[dict] = []
        for _ in range(20):
            try:
                msg = ws.receive_json()
            except (WebSocketDisconnect, RuntimeError, ValueError):
                break
            frames.append(msg)
            if msg.get("type") in ("done", "error"):
                break
    types = [f["type"] for f in frames]
    assert types[0] == "canonical"
    assert "postgres" in [t.get("dialect") for t in frames if t["type"] == "transpile"]
    assert "duckdb" in [t.get("dialect") for t in frames if t["type"] == "transpile"]
    assert types[-1] == "done"


def test_ws_ask_with_nl_rule_match():
    """Rule-based NL match should transpile without invoking the LLM."""
    from fastapi.testclient import TestClient
    from speaksql.service import app
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client, client.websocket_connect("/v1/ask") as ws:
        ws.send_json(
            {
                "question": "count of orders",
                "is_sql": False,
                "dialects": ["postgres"],
            }
        )
        frames: list[dict] = []
        for _ in range(20):
            try:
                msg = ws.receive_json()
            except (WebSocketDisconnect, RuntimeError, ValueError):
                break
            frames.append(msg)
            if msg.get("type") in ("done", "error"):
                break
    canonical = next(f for f in frames if f["type"] == "canonical")
    assert "COUNT" in canonical["sql"].upper()


def test_ws_ask_with_llm_streaming(monkeypatch):
    """When LLM is enabled, llm_token frames should precede canonical."""
    import speaksql.llm as llm_mod

    class ChunkedProvider:
        name = "chunked"

        def complete(self, messages):
            return "".join(self.stream(messages))

        def stream(self, messages):
            return iter(["SELECT ", "* ", "FROM ", "users"])

    monkeypatch.setattr(llm_mod, "make_provider", lambda: ChunkedProvider())

    # Re-import service so it picks up the patched make_provider
    import speaksql.service
    importlib.reload(speaksql.service)

    monkeypatch.setenv("SPEAKSQL_USE_LLM", "1")

    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(speaksql.service.app) as client, client.websocket_connect(
        "/v1/ask"
    ) as ws:
        ws.send_json(
            {
                "question": "xyzzy unknown",
                "is_sql": False,
                "dialects": ["postgres"],
            }
        )
        frames: list[dict] = []
        for _ in range(20):
            try:
                msg = ws.receive_json()
            except (WebSocketDisconnect, RuntimeError, ValueError):
                break
            frames.append(msg)
            if msg.get("type") in ("done", "error"):
                break

    token_frames = [f for f in frames if f["type"] == "llm_token"]
    assert len(token_frames) >= 1
    canonical = next(f for f in frames if f["type"] == "canonical")
    assert canonical["sql"] == "SELECT * FROM users"


def test_ws_ask_rejects_bad_dialect():
    """Asking for a non-supported dialect surfaces an error frame."""
    from fastapi.testclient import TestClient
    from speaksql.service import app
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client, client.websocket_connect("/v1/ask") as ws:
        ws.send_json(
            {
                "question": "SELECT 1",
                "is_sql": True,
                "dialects": ["oracle"],  # not in SUPPORTED_DIALECTS
            }
        )
        try:
            frame = ws.receive_json()
        except (WebSocketDisconnect, RuntimeError):
            frame = {"type": "error", "message": "connection closed before frame"}
    assert frame["type"] == "error"
    assert "unsupported dialect" in frame["message"].lower()


def test_ws_ask_rejects_empty_question():
    from fastapi.testclient import TestClient
    from speaksql.service import app
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client, client.websocket_connect("/v1/ask") as ws:
        ws.send_json(
            {
                "question": "",
                "is_sql": True,
                "dialects": ["postgres"],
            }
        )
        try:
            frame = ws.receive_json()
        except (WebSocketDisconnect, RuntimeError):
            frame = {"type": "error", "message": "connection closed before frame"}
    assert frame["type"] == "error"