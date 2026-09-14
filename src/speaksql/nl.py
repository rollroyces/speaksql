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

    Falls back to wrapping the question as a CTE name comment if no
    pattern matches — the caller can replace this with an LLM planner.
    """
    q = question.strip().rstrip("?.!")
    for pat, tmpl in _PATTERNS:
        m = pat.match(q)
        if m:
            try:
                return tmpl.format(**m.groupdict())
            except KeyError:
                continue
    # Honest fallback — let downstream emit a syntactically valid (if
    # semantically empty) plan with the question as a comment.
    return f"-- could not parse: {question}\nSELECT 1 AS placeholder"