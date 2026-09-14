"""NL layer tests."""

from __future__ import annotations

from speaksql.nl import nl_to_canonical


def test_count_of_pattern():
    out = nl_to_canonical("count of amount")
    assert "COUNT(amount)" in out
    assert "FROM events" in out


def test_top_n_pattern():
    out = nl_to_canonical("top 5 country by amount")
    assert "country" in out
    assert "LIMIT 5" in out


def test_monthly_pattern():
    out = nl_to_canonical("monthly amount by country")
    assert "DATE_TRUNC" in out.upper()
    assert "MONTH" in out.upper()


def test_unparseable_falls_back_gracefully():
    out = nl_to_canonical("what is the meaning of life?")
    assert "could not parse" in out.lower() or "placeholder" in out.lower()