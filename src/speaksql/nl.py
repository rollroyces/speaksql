"""NL → canonical ANSI SQL.

This module is intentionally pluggable. Default implementation is a
rule-based mapper for a small set of common patterns (counts, top-N,
monthly aggregates). Real systems swap in an LLM-backed planner.

Public API:
    nl_to_canonical(question: str) -> str
"""

from __future__ import annotations

import re

# Patterns are tried in order; first match wins. Keep them conservative
# — SpeakSQL is honest about what it handles without an LLM.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^count of (?P<col>\w+)$", re.IGNORECASE),
        "SELECT COUNT({col}) AS cnt FROM events",
    ),
    (
        re.compile(r"^top (?P<n>\d+) (?P<col>\w+) by (?P<by>\w+)$", re.IGNORECASE),
        "SELECT {col}, SUM({col}) AS total FROM events GROUP BY {col} ORDER BY total DESC LIMIT {n}",
    ),
    (
        re.compile(r"^monthly (?P<col>\w+) by (?P<by>\w+)$", re.IGNORECASE),
        (
            "SELECT DATE_TRUNC('month', created_at) AS month, SUM({col}) AS total "
            "FROM events GROUP BY month ORDER BY month"
        ),
    ),
)


def nl_to_canonical(question: str) -> str:
    """Translate a natural-language question into a canonical SQL string.

    Two-tier behavior:
    1. Rule-based patterns (`count of X`, `top N X by Y`, `monthly X by Y`)
       match first. These work without any external dependency.
    2. If `SPEAKSQL_USE_LLM=1` is set AND no rule matched, falls back to
       the LLM planner (`speaksql.llm`). If the LLM planner is disabled
       or also fails, returns a syntactically valid placeholder SQL with
       the question preserved as a comment.

    Honest about what it handles — never fabricates a meaningful SQL
    query from an ambiguous question.
    """
    q = question.strip().rstrip("?.!")
    for pat, tmpl in _PATTERNS:
        m = pat.match(q)
        if m:
            try:
                return tmpl.format(**m.groupdict())
            except KeyError:
                continue
    # Rule-based missed. Try LLM if enabled.
    try:
        from speaksql.llm import is_llm_enabled, llm_to_canonical
    except ImportError:
        is_llm_enabled = lambda: False
        llm_to_canonical = None  # type: ignore[assignment]
    if is_llm_enabled() and llm_to_canonical is not None:
        try:
            return llm_to_canonical(question)
        except Exception as e:  # noqa: BLE001
            # LLM unavailable / timed out / failed validation — log and fall through.
            import logging
            logging.getLogger(__name__).debug("LLM planner failed: %s", e)
    # Honest fallback — let downstream emit a syntactically valid (if
    # semantically empty) plan with the question as a comment.
    return f"-- could not parse: {question}\nSELECT 1 AS placeholder"