"""Tests for the LLM planner (mock + provider protocol).

We do NOT test against a live LLM endpoint — that's an integration concern
that depends on credentials. We test the protocol, the prompt shape, the
mock provider, the OpenAI-compatible provider against a fake HTTP server,
and the env-var wiring.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from speaksql import (
    LLMError,
    MockProvider,
    OpenAICompatibleProvider,
    llm_to_canonical,
    make_provider,
)
from speaksql.llm import SYSTEM_PROMPT, is_llm_enabled


def test_mock_provider_returns_canned_sql_for_known_phrase():
    p = MockProvider()
    sql = p.complete(
        [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "show all users"},
        ]
    )
    assert sql == "SELECT * FROM users"


def test_mock_provider_falls_back_for_unknown_phrase():
    p = MockProvider()
    sql = p.complete(
        [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "gimme the frobnitz of something"},
        ]
    )
    assert sql == "SELECT 1 AS placeholder"


def test_mock_provider_is_deterministic():
    """No RNG — same input yields same output."""
    p1 = MockProvider()
    p2 = MockProvider()
    msgs = [{"role": "user", "content": "count of orders per customer"}]
    assert p1.complete(msgs) == p2.complete(msgs)


def test_make_provider_returns_mock_without_env():
    """No env vars set → MockProvider."""
    import os

    for k in list(os.environ):
        if k.startswith("SPEAKSQL_LLM_") or k == "OPENAI_API_KEY":
            del os.environ[k]
    p = make_provider()
    assert isinstance(p, MockProvider)


def test_make_provider_returns_openai_when_configured(monkeypatch):
    monkeypatch.setenv("SPEAKSQL_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("SPEAKSQL_LLM_API_KEY", "sk-test-xxx")
    monkeypatch.setenv("SPEAKSQL_LLM_MODEL", "gpt-4o-mini")
    p = make_provider()
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.base_url == "https://api.openai.com/v1"
    assert p.model == "gpt-4o-mini"


def test_make_provider_errors_when_base_url_set_without_key(monkeypatch):
    monkeypatch.setenv("SPEAKSQL_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.delenv("SPEAKSQL_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError):
        make_provider()


def test_is_llm_enabled_false_by_default(monkeypatch):
    monkeypatch.delenv("SPEAKSQL_USE_LLM", raising=False)
    assert is_llm_enabled() is False


def test_is_llm_enabled_truthy_values(monkeypatch):
    for v in ("1", "true", "yes", "TRUE", "Yes"):
        monkeypatch.setenv("SPEAKSQL_USE_LLM", v)
        assert is_llm_enabled() is True


def test_system_prompt_constrains_output():
    assert "ANSI" in SYSTEM_PROMPT
    assert "DATE_TRUNC" in SYSTEM_PROMPT
    assert "LIMIT" in SYSTEM_PROMPT


def test_llm_to_canonical_uses_injected_provider():
    p = MockProvider()
    sql = llm_to_canonical("show all users", provider=p)
    assert sql == "SELECT * FROM users"


def test_llm_to_canonical_strips_whitespace():
    # The mock returns canned text without whitespace padding; verify that
    # whatever the provider returns, llm_to_canonical trims it.

    class PaddedProvider:
        name = "padded"

        def complete(self, _):
            return "  \n  SELECT * FROM users  \n  "

    sql = llm_to_canonical("show all users", provider=PaddedProvider())
    assert sql == "SELECT * FROM users"


class _Handler(BaseHTTPRequestHandler):
    """Tiny test HTTP server that mimics OpenAI's /chat/completions."""

    # Class-level state shared across all instances (single-server test).
    # Mutable by design — tests deliberately swap this out per fixture.
    payload: ClassVar[dict] = {
        "choices": [{"message": {"content": "SELECT * FROM canned_test_response"}}]
    }
    received_requests: ClassVar[list[dict]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        try:
            self.received_requests.append(json.loads(body))
        except json.JSONDecodeError:
            self.received_requests.append({"raw": body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.payload).encode("utf-8"))

    def log_message(self, *_args: object) -> None:  # silence stderr noise
        pass


@pytest.fixture
def fake_openai_server():
    # SO_REUSEADDR prevents TIME_WAIT collisions when multiple tests spin
    # up + tear down a server on a kernel-assigned port in quick succession.
    class ReuseAddrHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReuseAddrHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.received_requests = []
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def test_stream_method_yields_chunks_from_real_provider():
    """The OpenAICompatibleProvider's stream() should yield SSE chunks."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from speaksql.llm import OpenAICompatibleProvider

    chunks_data = [
        {"choices": [{"delta": {"content": "SELECT "}}]},
        {"choices": [{"delta": {"content": "DATE_TRUNC("}}]},
        {"choices": [{"delta": {"content": "'month', ts)"}}]},
    ]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for c in chunks_data:
                line = f"data: {json.dumps(c)}\n\n".encode()
                self.wfile.write(line)
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *args):
            pass

    # SO_REUSEADDR so this port can be re-bound quickly across test runs.
    class ReuseHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReuseHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        p = OpenAICompatibleProvider(
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            model="test",
            api_key="sk-test",
        )
        chunks = list(p.stream([{"role": "user", "content": "x"}]))
        assert chunks == ["SELECT ", "DATE_TRUNC(", "'month', ts)"]
        # Note: complete() won't work against this SSE-only fake server
        # because it tries to JSON-decode the SSE payload. That's an
        # expected limitation — clients pick one or the other per request.
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_mock_provider_stream_yields_full_string():
    """MockProvider.stream() should yield the full response in one chunk."""
    from speaksql.llm import MockProvider

    p = MockProvider()
    chunks = list(p.stream([{"role": "user", "content": "show all users"}]))
    assert chunks == ["SELECT * FROM users"]


def test_llm_to_canonical_streaming_returns_iterable():
    """llm_to_canonical_streaming returns an iterable that yields chunks."""
    from speaksql.llm import (
        MockProvider,
        llm_to_canonical_streaming,
    )

    chunks = list(
        llm_to_canonical_streaming("show all users", provider=MockProvider())
    )
    # Mock provider yields the full string in one chunk
    assert chunks == ["SELECT * FROM users"]


def test_openai_provider_against_fake_server(fake_openai_server):
    port = fake_openai_server.server_address[1]
    p = OpenAICompatibleProvider(
        base_url=f"http://127.0.0.1:{port}",
        model="test-model",
        api_key="sk-test",
    )
    out = p.complete([{"role": "user", "content": "hello"}])
    assert out == "SELECT * FROM canned_test_response"

    # Verify the request shape we sent
    req = _Handler.received_requests[-1]
    assert req["model"] == "test-model"
    assert req["messages"][-1]["content"] == "hello"


def test_openai_provider_handles_400(fake_openai_server):
    # Override handler to return 400
    class BadHandler(_Handler):
        def do_POST(self) -> None:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "bad request"}')

    fake_openai_server.shutdown()
    server = HTTPServer(("127.0.0.1", 0), BadHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        p = OpenAICompatibleProvider(
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            model="x",
            api_key="sk-test",
            timeout_s=2,
        )
        with pytest.raises(LLMError):
            p.complete([{"role": "user", "content": "x"}])
    finally:
        server.shutdown()
        thread.join()


def test_openai_provider_malformed_response(fake_openai_server):
    class BadHandler(_Handler):
        def do_POST(self) -> None:
            # Write the malformed body BEFORE calling end_headers so the
            # client doesn't see a half-closed response. Use Content-Length
            # explicitly so the client knows how much to read.
            body = b"not json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

    fake_openai_server.shutdown()
    server = HTTPServer(("127.0.0.1", 0), BadHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        p = OpenAICompatibleProvider(
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            model="x",
            api_key="sk-test",
            timeout_s=2,
        )
        with pytest.raises(LLMError):
            p.complete([{"role": "user", "content": "x"}])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_nl_layer_uses_llm_when_enabled(monkeypatch):
    """End-to-end: when SPEAKSQL_USE_LLM=1 and rule misses, MockProvider kicks in."""
    monkeypatch.setenv("SPEAKSQL_USE_LLM", "1")
    # 'show all users' has a mock trigger
    from speaksql.nl import nl_to_canonical

    out = nl_to_canonical("show all users")
    assert "SELECT * FROM users" in out


def test_nl_layer_bypasses_llm_when_rule_matches(monkeypatch):
    """Even with LLM enabled, rule-based matches take precedence."""
    monkeypatch.setenv("SPEAKSQL_USE_LLM", "1")
    from speaksql.nl import nl_to_canonical

    out = nl_to_canonical("count of orders")
    assert "COUNT(orders)" in out
    assert "SELECT * FROM users" not in out  # mock not consulted


def test_nl_layer_falls_back_when_llm_disabled(monkeypatch):
    """Without SPEAKSQL_USE_LLM, no LLM call, placeholder returned."""
    monkeypatch.delenv("SPEAKSQL_USE_LLM", raising=False)
    from speaksql.nl import nl_to_canonical

    out = nl_to_canonical("xyzzy unknown query")
    assert "-- could not parse:" in out
    assert "SELECT 1 AS placeholder" in out