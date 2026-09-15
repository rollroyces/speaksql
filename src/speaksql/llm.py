"""LLM-backed NL → canonical SQL planner.

This module is **opt-in** — by default, SpeakSQL uses the rule-based
planner in `speaksql.nl`. The LLM planner only kicks in when:

    SPEAKSQL_USE_LLM=1

…and then only for inputs the rule-based planner declines to handle
(it doesn't match any of the canned regex patterns).

The design goals:

1. **No SDK dependency.** Uses stdlib `urllib.request` so we don't pull in
   `openai` / `anthropic` / etc. as a base-install dependency.
2. **Provider-agnostic.** Any OpenAI-compatible HTTP endpoint works —
   OpenAI, Together, Groq, OpenRouter, local llama.cpp, vLLM, Ollama.
3. **Strict prompt.** The system prompt constrains output to canonical
   ANSI SQL only — no dialect-specific syntax, no markdown, no
   explanation. Parsing failures fall back to the rule-based result.
4. **Mockable.** `MockProvider` returns canned canonical SQL so tests
   don't require a network round-trip.
5. **Bounded.** Hard cap on tokens + timeout; failures are surfaced as
   `LLMError` (a `SpeakSQLError` subclass) rather than silently broken.

Usage:

    import os
    os.environ["SPEAKSQL_USE_LLM"] = "1"
    os.environ["SPEAKSQL_LLM_BASE_URL"] = "https://api.openai.com/v1"
    os.environ["SPEAKSQL_LLM_MODEL"] = "gpt-4o-mini"
    os.environ["OPENAI_API_KEY"] = "sk-..."  # any bearer-token header name works

    from speaksql.llm import llm_to_canonical
    sql = llm_to_canonical("monthly active users")

See README for the full list of env vars.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from speaksql.exceptions import SpeakSQLError

SYSTEM_PROMPT = """\
You are a SQL generator. Translate natural-language questions into a single
canonical ANSI SQL query.

OUTPUT RULES:
1. Output ONLY the SQL — no markdown, no explanation, no backticks.
2. Use ANSI-compatible syntax only. No dialect-specific functions or types.
   - DATE_TRUNC('month', column) for month bucketing
   - Use LIMIT N (not TOP N)
   - Use NULLS FIRST / NULLS LAST explicitly in ORDER BY when it matters
3. Use single quotes for string literals; double quotes for identifiers
   only if needed.
4. Default table name when the question is ambiguous: assume a sensible
   table name from the question (singular noun, lowercase). E.g.
   "show users" → SELECT * FROM users.
5. Keep the query simple. Prefer explicit column lists over SELECT *
   unless the question explicitly says "all".
"""

# A small handful of worked examples to anchor the output style.
# These keep the prompt short while demonstrating the canonical dialect.
FEW_SHOT_EXAMPLES: tuple[tuple[str, str], ...] = (
    (
        "show all users",
        "SELECT * FROM users",
    ),
    (
        "count of orders per customer",
        "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id",
    ),
    (
        "monthly revenue from the orders table",
        (
            "SELECT DATE_TRUNC('month', created_at) AS month, "
            "SUM(amount) AS revenue FROM orders GROUP BY month ORDER BY month"
        ),
    ),
    (
        "users who never placed an order",
        (
            "SELECT u.* FROM users u LEFT JOIN orders o "
            "ON u.id = o.user_id WHERE o.id IS NULL"
        ),
    ),
)


class LLMError(SpeakSQLError):
    """Raised when the LLM provider is unavailable, errors, or returns unparsable SQL."""


class LLMProvider(Protocol):
    """Minimal interface a provider must implement.

    `messages` is a list of role/content dicts in OpenAI shape:
        [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    """

    name: str

    def complete(self, messages: Sequence[dict[str, str]]) -> str:
        """Return the model's raw text response. Raise LLMError on any failure."""
        ...

    def stream(self, messages: Sequence[dict[str, str]]) -> Iterable[str]:
        """Yield model output incrementally as text chunks arrive.

        Default implementation falls back to `complete()` and yields the
        full response in a single chunk. Real streaming providers should
        override to yield partial deltas from the network.
        """
        yield self.complete(messages)


@dataclass
class MockProvider:
    """Returns canned canonical SQL for testing.

    Picks the first few-shot example whose input appears as a substring
    of the user's question; otherwise returns the generic fallback
    `SELECT 1 AS placeholder`. Intentionally deterministic — no RNG.
    """

    name: str = "mock"

    def complete(self, messages: Sequence[dict[str, str]]) -> str:
        user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        question = user_msg.lower()
        for trigger, sql in FEW_SHOT_EXAMPLES:
            if trigger.lower() in question:
                return sql
        return "SELECT 1 AS placeholder"

    def stream(self, messages: Sequence[dict[str, str]]) -> Iterable[str]:
        yield self.complete(messages)


@dataclass
class OpenAICompatibleProvider:
    """OpenAI-shaped HTTP provider.

    POST {base_url}/chat/completions with bearer auth. Works with:
        - OpenAI:   base_url=https://api.openai.com/v1, header=Authorization: Bearer ...
        - OpenRouter / Together / Groq / vLLM / Ollama: similar shapes.

    Env vars consumed (none required if the caller passes everything explicitly):
        - SPEAKSQL_LLM_BASE_URL (default https://api.openai.com/v1)
        - SPEAKSQL_LLM_MODEL (default gpt-4o-mini)
        - SPEAKSQL_LLM_API_KEY (fallback OPENAI_API_KEY)
        - SPEAKSQL_LLM_AUTH_HEADER (default "Authorization")
        - SPEAKSQL_LLM_AUTH_PREFIX (default "Bearer ")
        - SPEAKSQL_LLM_TIMEOUT_S (default 30)
        - SPEAKSQL_LLM_MAX_TOKENS (default 512)
        - SPEAKSQL_LLM_TEMPERATURE (default 0.0)
    """

    base_url: str
    model: str
    api_key: str
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    timeout_s: float = 30.0
    max_tokens: int = 512
    temperature: float = 0.0
    name: str = "openai-compatible"

    def complete(self, messages: Sequence[dict[str, str]]) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                self.auth_header: f"{self.auth_prefix}{self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            raise LLMError(f"LLM request failed: {e}") from e
        try:
            data = json.loads(body)
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"LLM response malformed: {e}\nbody={body[:200]}") from e

    def stream(self, messages: Sequence[dict[str, str]]) -> Iterable[str]:
        """Yield incremental text deltas from the OpenAI-compatible endpoint.

        Sets `stream=True` in the request payload and parses the
        Server-Sent-Events-style response (lines of `data: {...}` JSON).
        Yields each `delta.content` text chunk as it arrives.

        Yields `LLMError` text if the network call fails — callers
        iterating over the result should treat each yield as a
        partial chunk, not as a final response.
        """
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                self.auth_header: f"{self.auth_prefix}{self.api_key}",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout_s)
        except urllib.error.URLError as e:
            raise LLMError(f"LLM stream request failed: {e}") from e

        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload_str = line[len("data:"):].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                delta = (
                    chunk.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content")
                )
                if delta:
                    yield delta
        finally:
            resp.close()


def is_llm_enabled() -> bool:
    """True when the LLM planner should be used as a fallback."""
    return os.environ.get("SPEAKSQL_USE_LLM", "").strip().lower() in ("1", "true", "yes")


def make_provider() -> LLMProvider:
    """Construct the configured provider from env vars.

    Defaults to `MockProvider` (returns canned SQL) when no real
    credentials are configured. Switch to OpenAICompatibleProvider by
    setting `SPEAKSQL_LLM_BASE_URL` and `SPEAKSQL_LLM_API_KEY`.

    Raises:
        LLMError: provider is configured but missing required credentials.
    """
    base_url = os.environ.get("SPEAKSQL_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("SPEAKSQL_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if base_url and api_key:
        return OpenAICompatibleProvider(
            base_url=base_url,
            model=os.environ.get("SPEAKSQL_LLM_MODEL", "gpt-4o-mini"),
            api_key=api_key,
            auth_header=os.environ.get("SPEAKSQL_LLM_AUTH_HEADER", "Authorization"),
            auth_prefix=os.environ.get("SPEAKSQL_LLM_AUTH_PREFIX", "Bearer "),
            timeout_s=float(os.environ.get("SPEAKSQL_LLM_TIMEOUT_S", "30")),
            max_tokens=int(os.environ.get("SPEAKSQL_LLM_MAX_TOKENS", "512")),
            temperature=float(os.environ.get("SPEAKSQL_LLM_TEMPERATURE", "0.0")),
        )
    if base_url and not api_key:
        raise LLMError(
            "SPEAKSQL_LLM_BASE_URL is set but no API key was found. "
            "Set SPEAKSQL_LLM_API_KEY (or OPENAI_API_KEY)."
        )
    return MockProvider()


def _build_messages(
    question: str, schema_hint: str | None = None
) -> list[dict[str, str]]:
    """Assemble the OpenAI-shaped message list for the LLM call.

    System prompt + few-shot examples + the user's question. Kept in a
    helper so complete() and stream() stay in lockstep.

    If `schema_hint` is provided, it is appended to the system prompt as
    a "Schema context" block — that's how FK-aware planning flows into
    the LLM even when the rule layer missed.
    """
    system = SYSTEM_PROMPT
    if schema_hint:
        system += "\n\n## Schema context\n" + schema_hint
    return [
        {"role": "system", "content": system},
        *({"role": "user", "content": q} for q, _ in FEW_SHOT_EXAMPLES),
        *({"role": "assistant", "content": sql} for _, sql in FEW_SHOT_EXAMPLES),
        {"role": "user", "content": question},
    ]


def llm_to_canonical(
    question: str,
    provider: LLMProvider | None = None,
    schema_hint: str | None = None,
) -> str:
    """Translate a natural-language question into canonical ANSI SQL via LLM.

    If `provider` is None, `make_provider()` is called — which reads env
    vars and returns either a real OpenAI-compatible client or a
    `MockProvider` (depending on what's configured).

    Pass `schema_hint` (typically `SchemaHint.to_prompt_section()`) to
    inject a schema summary into the system prompt — useful when the
    FK-aware planner has already pinned down which tables are relevant.

    The returned string is whatever the model produced; we strip leading
    whitespace and trailing whitespace but don't try to parse/validate.
    Validation happens downstream in `plan()`.
    """
    provider = provider or make_provider()
    raw = provider.complete(_build_messages(question, schema_hint=schema_hint))
    return raw.strip()


def llm_to_canonical_streaming(
    question: str,
    provider: LLMProvider | None = None,
    schema_hint: str | None = None,
) -> Iterable[str]:
    """Streaming variant of `llm_to_canonical`.

    Yields text chunks from the underlying provider as they arrive. The
    caller is responsible for joining chunks if a complete string is
    needed. Raises `LLMError` if the underlying stream fails; the
    exception propagates out of the generator at iteration time.
    """
    provider = provider or make_provider()
    return provider.stream(_build_messages(question, schema_hint=schema_hint))


__all__ = [
    "FEW_SHOT_EXAMPLES",
    "SYSTEM_PROMPT",
    "LLMError",
    "LLMProvider",
    "MockProvider",
    "OpenAICompatibleProvider",
    "is_llm_enabled",
    "llm_to_canonical",
    "llm_to_canonical_streaming",
    "make_provider",
]