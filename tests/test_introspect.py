"""Schema-introspection tests."""

from __future__ import annotations

from speaksql.introspect import introspect, supported


def test_supported_returns_tuple():
    s = supported()
    assert isinstance(s, tuple)
    assert "postgres" in s


def test_introspect_returns_schema_list():
    schema = introspect("postgres")
    assert len(schema.tables) >= 1
    events = schema.find("events")
    assert events is not None
    cols = {c.name for c in events.columns}
    assert {"id", "event_name", "amount"}.issubset(cols)


def test_introspect_for_each_supported_dialect():
    for d in supported():
        schema = introspect(d)
        assert len(schema.tables) >= 1, d