"""SpeakSQL — Speak once. Query any dialect.

A dialect-aware NL-to-SQL transpiler. Build a canonical ANSI query plan
and emit it correctly for Postgres, MySQL, SQL Server, Snowflake, BigQuery,
Databricks (Spark), DuckDB, and SAP HANA.
"""

from __future__ import annotations

from speaksql.core import SUPPORTED_DIALECTS, Dialect, Plan, emit, plan, transpile
from speaksql.diff import DiffEntry, format_diff, semantic_diff
from speaksql.exceptions import SpeakSQLError, TranspileError, UnsupportedDialectError
from speaksql.graph import JoinGraph, build_join_graph, render_html
from speaksql.introspect import introspect

# Lazy-import LLM module so its (optional) usage of urllib doesn't bite
# anything until the user actually calls it.
try:
    from speaksql.llm import (
        LLMError,
        LLMProvider,
        MockProvider,
        OpenAICompatibleProvider,
        is_llm_enabled,
        llm_to_canonical,
        llm_to_canonical_streaming,
        llm_to_canonical_streaming_async,
        make_provider,
    )
except ImportError:  # pragma: no cover
    pass

__version__ = "0.1.0"
__all__ = [
    "SUPPORTED_DIALECTS",
    "Dialect",
    "DiffEntry",
    "JoinGraph",
    "LLMError",
    "LLMProvider",
    "MockProvider",
    "OpenAICompatibleProvider",
    "Plan",
    "SpeakSQLError",
    "TranspileError",
    "UnsupportedDialectError",
    "__version__",
    "build_join_graph",
    "emit",
    "format_diff",
    "introspect",
    "is_llm_enabled",
    "llm_to_canonical",
    "llm_to_canonical_streaming",
    "llm_to_canonical_streaming_async",
    "make_provider",
    "plan",
    "render_html",
    "semantic_diff",
    "transpile",
]