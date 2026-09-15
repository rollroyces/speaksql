"""NL → canonical ANSI SQL.

This module is intentionally pluggable. Default implementation is a
rule-based mapper for a small set of common patterns (counts, top-N,
monthly aggregates). When a `schema` with FK metadata is provided,
the FK-aware planner (`speaksql.schema_aware`) augments the rule
patterns with real JOIN edges. Real systems swap in an LLM-backed
planner via `SPEAKSQL_USE_LLM=1`.

Public API:
    nl_to_canonical(question: str, schema: SchemaList | None = None) -> str
"""

from __future__ import annotations

import re

# Patterns are tried in order; first match wins. Keep them conservative
# — SpeakSQL is honest about what it handles without an LLM.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^count of (?P<col>\w+)$", re.IGNORECASE),
        "SELECT COUNT({col}) FROM {table}",
    ),
    (
        re.compile(r"^count of (?P<col>\w+) per (?P<by>\w+)$", re.IGNORECASE),
        "SELECT {by}, COUNT({col}) AS cnt FROM {table} GROUP BY {by} ORDER BY cnt DESC",
    ),
    (
        re.compile(r"^top (?P<n>\d+) (?P<col>\w+) by (?P<by>\w+)$", re.IGNORECASE),
        "SELECT {col}, SUM({col}) AS total FROM {table} GROUP BY {col} ORDER BY total DESC LIMIT {n}",
    ),
    (
        re.compile(r"^monthly (?P<col>\w+) by (?P<by>\w+)$", re.IGNORECASE),
        (
            "SELECT DATE_TRUNC('month', created_at) AS month, SUM({col}) AS total "
            "FROM {table} GROUP BY month ORDER BY month"
        ),
    ),
)


def nl_to_canonical(question: str, schema: object | None = None) -> str:
    """Translate a natural-language question into a canonical SQL string.

    Three-tier behavior:
    1. If `schema` is provided AND has FK metadata, run the FK-aware
       planner (`speaksql.schema_aware`) to pick relevant tables + joins,
       then enrich the rule patterns with that context. Falls through
       to step 2 if nothing matches.
    2. Rule-based patterns (`count of X`, `top N X by Y`, `monthly X by Y`)
       match. The default table is the top-ranked table from the schema
       hint (when present), or `events`.
    3. If `SPEAKSQL_USE_LLM=1` is set AND no rule matched, falls back to
       the LLM planner. If disabled, returns a syntactically valid
       placeholder SQL with the question preserved as a comment.

    Honest about what it handles — never fabricates a meaningful SQL
    query from an ambiguous question.
    """
    from speaksql.introspect import SchemaList

    q = question.strip().rstrip("?.!")

    # FK-aware enrichment: pick the default table + which joins to add.
    default_table = "events"
    join_clause = ""
    schema_hint_text = ""
    if isinstance(schema, SchemaList) and schema.tables:
        try:
            from speaksql.schema_aware import plan_for_question

            hint = plan_for_question(q, schema)
            if hint.tables:
                default_table = hint.tables[0]
                # Render JOINs for any FK edges between chosen tables.
                if hint.joins:
                    from speaksql.schema_aware import joins_to_sql

                    join_clause = "\n" + joins_to_sql(hint.joins)
                schema_hint_text = hint.to_prompt_section()
        except (ImportError, AttributeError, ValueError, TypeError):
            # If the planner crashes, fall through to the rules with the
            # default table rather than breaking NL entirely.
            import logging

            logging.getLogger(__name__).debug("FK-aware planner failed", exc_info=True)

    for pat, tmpl in _PATTERNS:
        m = pat.match(q)
        if m:
            try:
                formatted = tmpl.format(table=default_table, **m.groupdict())
                return formatted + join_clause
            except KeyError:
                continue
    # Rule-based missed. Try LLM if enabled.
    try:
        from speaksql.llm import (
            is_llm_enabled,
            llm_to_canonical,
        )
    except ImportError:
        is_llm_enabled = lambda: False
        llm_to_canonical = None  # type: ignore[assignment]
    if is_llm_enabled() and llm_to_canonical is not None:
        try:
            return llm_to_canonical(q, schema_hint=schema_hint_text or None)
        except Exception as e:  # noqa: BLE001
            # LLM unavailable / timed out / failed validation — log and fall through.
            import logging

            logging.getLogger(__name__).debug("LLM planner failed: %s", e)
    # Honest fallback — let downstream emit a syntactically valid (if
    # semantically empty) plan with the question as a comment.
    return f"-- could not parse: {question}\nSELECT 1 AS placeholder"