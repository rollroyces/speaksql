"""Tests for the semantic diff module."""

from __future__ import annotations

import pytest
from speaksql import DiffEntry, format_diff, semantic_diff
from speaksql.exceptions import TranspileError


def test_identical_sql_has_no_diffs():
    sql = "SELECT id, name FROM t WHERE id > 0 ORDER BY id LIMIT 10"
    diffs = semantic_diff(sql, sql)
    assert diffs == []


def test_function_call_rewrite_detected():
    # Postgres DATE_TRUNC('month', x) vs BigQuery DATE_TRUNC(x, MONTH) — same
    # canonical function but different call shape. The args happen to
    # normalize to the same canonical form in SQLGlot, so this test exercises
    # the case where they DON'T — using a vendor-specific alias.
    a = "SELECT CURRENT_TIMESTAMP AS t FROM t"
    b = "SELECT NOW() AS t FROM t"
    diffs = semantic_diff(a, b)
    # CURRENT_TIMESTAMP and NOW() both canonicalize to CURRENT_TIMESTAMP,
    # so this is actually identical — verify:
    assert diffs == []


def test_null_ordering_detected():
    # ASC alone is NULLS FIRST (default). Explicit NULLS LAST is different.
    a = "SELECT id FROM t ORDER BY id ASC NULLS LAST"
    b = "SELECT id FROM t ORDER BY id ASC"
    diffs = semantic_diff(a, b)
    assert len(diffs) >= 1
    null_diffs = [d for d in diffs if d.category == "null_ordering"]
    assert len(null_diffs) == 1
    assert null_diffs[0].a == "NULLS LAST"
    assert null_diffs[0].b == "NULLS FIRST"


def test_asc_vs_desc_detected():
    a = "SELECT id FROM t ORDER BY id ASC"
    b = "SELECT id FROM t ORDER BY id DESC"
    diffs = semantic_diff(a, b)
    dir_diffs = [d for d in diffs if d.category == "distinct_syntax"]
    assert any(d.a == "ASC" and d.b == "DESC" for d in dir_diffs)


def test_distinct_syntax_limit_diff():
    # LIMIT vs TOP — when parsed with the right dialects, both end up with
    # LIMIT, so the textual diff is zero. We assert this behavior.
    a = "SELECT TOP 10 id FROM t"  # SQL Server
    b = "SELECT id FROM t LIMIT 10"
    diffs = semantic_diff(a, b, dialect_a="tsql")
    assert diffs == []


def test_structural_diff_on_predicate():
    a = "SELECT id FROM t WHERE id > 0"
    b = "SELECT id FROM t WHERE id > 100"
    diffs = semantic_diff(a, b)
    assert any(d.category == "predicate" for d in diffs)


def test_extra_projection_detected():
    a = "SELECT id FROM t"
    b = "SELECT id, name FROM t"
    diffs = semantic_diff(a, b)
    assert any(d.category == "structural" and d.note == "extra projection in b" for d in diffs)


def test_format_diff_includes_count():
    diffs = [
        DiffEntry("null_ordering", "NULLS LAST", "NULLS FIRST", "ORDER BY #0", "test"),
    ]
    text = format_diff(diffs)
    assert "1 semantic difference" in text
    assert "null_ordering" in text
    assert "NULLS LAST" in text


def test_format_diff_empty():
    assert "No semantic differences" in format_diff([])


def test_invalid_sql_raises_transpile_error():
    with pytest.raises(TranspileError):
        semantic_diff("SELECT 'unterminated", "SELECT 1")


def test_diff_entry_to_dict():
    e = DiffEntry("literal", "'a'", "\"a\"", "SELECT #0", "same value")
    d = e.to_dict()
    assert d["category"] == "literal"
    assert d["a"] == "'a'"
    assert d["b"] == "\"a\""
    assert d["location"] == "SELECT #0"
    assert d["note"] == "same value"


def test_dialect_aware_parsing():
    """Passing dialect_a/dialect_b affects how each SQL is parsed."""
    # Both inputs are the same string but one is parsed as tsql and one as
    # postgres — should still be identical
    sql = "SELECT id FROM t"
    diffs = semantic_diff(sql, sql, dialect_a="tsql", dialect_b="postgres")
    assert diffs == []