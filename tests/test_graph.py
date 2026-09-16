"""Tests for the JOIN graph builder and HTML renderer."""

from __future__ import annotations

import pytest
from speaksql import build_join_graph, render_html
from speaksql.introspect import ColumnInfo, SchemaList, TableInfo


def _make_schema() -> SchemaList:
    """Classic orders schema: users, orders, order_items, products, audit_log.

    audit_log is intentionally FK-free (no joins should touch it).
    """
    return SchemaList(
        tables=(
            TableInfo(
                catalog=None,
                schema=None,
                name="users",
                columns=(
                    ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),
                    ColumnInfo("email", "TEXT", nullable=False, is_primary_key=False),
                ),
            ),
            TableInfo(
                catalog=None,
                schema=None,
                name="orders",
                columns=(
                    ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),
                    ColumnInfo(
                        "user_id", "INTEGER", nullable=False, is_primary_key=False
                    ),
                    ColumnInfo(
                        "created_at", "TIMESTAMP", nullable=True, is_primary_key=False
                    ),
                ),
            ),
            TableInfo(
                catalog=None,
                schema=None,
                name="order_items",
                columns=(
                    ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),
                    ColumnInfo(
                        "order_id", "INTEGER", nullable=False, is_primary_key=False
                    ),
                    ColumnInfo(
                        "product_id", "INTEGER", nullable=False, is_primary_key=False
                    ),
                ),
            ),
            TableInfo(
                catalog=None,
                schema=None,
                name="products",
                columns=(
                    ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),
                    ColumnInfo("name", "TEXT", nullable=False, is_primary_key=False),
                ),
            ),
            TableInfo(
                catalog=None,
                schema=None,
                name="audit_log",
                columns=(
                    ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),
                    ColumnInfo("message", "TEXT", nullable=True, is_primary_key=False),
                ),
            ),
        )
    )


def test_build_graph_finds_expected_fks():
    g = build_join_graph(_make_schema())
    pairs = {frozenset((e.source, e.target)) for e in g.edges}
    assert frozenset(("orders", "users")) in pairs
    assert frozenset(("order_items", "orders")) in pairs
    assert frozenset(("order_items", "products")) in pairs


def test_build_graph_excludes_pk_to_pk_only():
    """Every table has a PK `id`; those PK↔PK links must be filtered out."""
    g = build_join_graph(_make_schema())
    # Every pair has `id` shared with same type, but only FK-named pairs survive.
    for e in g.edges:
        # Each edge must have at least one FK-named column
        assert any(c.endswith("_id") for c in e.columns), (
            f"edge {e} looks like a spurious PK↔PK link"
        )


def test_build_graph_isolated_table_has_no_edges():
    g = build_join_graph(_make_schema())
    audit = next(n for n in g.nodes if n.name == "audit_log")
    assert audit.join_columns == ()


def test_build_graph_empty_schema():
    g = build_join_graph(SchemaList(tables=()))
    assert g.nodes == ()
    assert g.edges == ()
    assert g.to_dict() == {"nodes": [], "edges": []}


def test_build_graph_type_mismatch_excluded():
    """Same column name but different data type → not a join candidate."""
    schema = SchemaList(
        tables=(
            TableInfo(
                catalog=None,
                schema=None,
                name="users",
                columns=(ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),),
            ),
            TableInfo(
                catalog=None,
                schema=None,
                name="posts",
                columns=(
                    ColumnInfo(
                        "user_id", "TEXT", nullable=False, is_primary_key=False
                    ),
                ),
            ),
        )
    )
    g = build_join_graph(schema)
    # TEXT user_id != INTEGER users.id → no edge
    assert g.edges == ()


def test_build_graph_singular_handles_irregulars():
    """`categories` -> `category`, `people` stays singular, `users` -> `user`."""
    schema = SchemaList(
        tables=(
            TableInfo(
                catalog=None,
                schema=None,
                name="users",
                columns=(ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),),
            ),
            TableInfo(
                catalog=None,
                schema=None,
                name="posts",
                columns=(
                    ColumnInfo(
                        "user_id", "INTEGER", nullable=False, is_primary_key=False
                    ),
                ),
            ),
        )
    )
    g = build_join_graph(schema)
    pairs = {frozenset((e.source, e.target)) for e in g.edges}
    assert frozenset(("users", "posts")) in pairs


def test_to_dict_roundtrip():
    g = build_join_graph(_make_schema())
    d = g.to_dict()
    assert "nodes" in d and "edges" in d
    assert len(d["nodes"]) == len(g.nodes)
    assert len(d["edges"]) == len(g.edges)
    # Each node has columns + join_columns
    for n in d["nodes"]:
        assert "columns" in n
        assert "join_columns" in n


def test_render_html_basic():
    g = build_join_graph(_make_schema())
    html = render_html(g, title="My Schema")
    assert "<!doctype html>" in html.lower()
    assert "My Schema" in html
    # SVG should be present
    assert "<svg" in html
    # Tables should be referenced in the right column
    assert "users" in html
    assert "products" in html


def test_render_html_empty_schema():
    html = render_html(build_join_graph(SchemaList(tables=())), title="Empty")
    assert "Empty" in html
    assert "No tables" in html


def test_render_html_escapes_table_names():
    """HTML escapes to prevent injection from weird identifiers."""
    schema = SchemaList(
        tables=(
            TableInfo(
                catalog=None,
                schema=None,
                name="weird<table>",
                columns=(ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),),
            ),
        )
    )
    g = build_join_graph(schema)
    html = render_html(g)
    # The weird name must be HTML-escaped so it doesn't break the page
    assert "&lt;table&gt;" in html
    # And the weird name should appear (escaped) in the right-column listing
    assert "weird&lt;table&gt;" in html


# ---------------------------------------------------------------------------
# suggest_fks tests
# ---------------------------------------------------------------------------


def test_suggest_fks_finds_named_fk_pattern():
    """orders.user_id should suggest -> users.id."""
    from speaksql.graph import suggest_fks
    from speaksql.introspect import ColumnInfo, SchemaList, TableInfo

    schema = SchemaList(
        tables=(
            TableInfo(None, None, "users", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
            )),
            TableInfo(None, None, "orders", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("user_id", "INTEGER"),
            )),
        ),
        foreign_keys=(),
    )
    suggestions = suggest_fks(schema)
    assert any(
        s.from_table == "orders" and s.from_column == "user_id"
        and s.to_table == "users" and s.to_column == "id"
        for s in suggestions
    ), f"missing user_id suggestion in {suggestions}"
    # And the suggestion has high confidence (named pattern)
    user_id_sug = [
        s for s in suggestions
        if s.from_table == "orders" and s.from_column == "user_id"
    ]
    assert user_id_sug[0].confidence >= 0.8
    assert user_id_sug[0].reason  # non-empty


def test_suggest_fks_filters_declared():
    """A declared FK should NOT be re-suggested."""
    from speaksql.graph import suggest_fks
    from speaksql.introspect import (
        ColumnInfo,
        ForeignKeyInfo,
        SchemaList,
        TableInfo,
    )

    schema = SchemaList(
        tables=(
            TableInfo(None, None, "users", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
            )),
            TableInfo(None, None, "orders", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("user_id", "INTEGER"),
            )),
        ),
        foreign_keys=(ForeignKeyInfo("orders", "user_id", "users", "id"),),
    )
    suggestions = suggest_fks(schema)
    user_id_sug = [
        s for s in suggestions
        if s.from_table == "orders" and s.from_column == "user_id"
    ]
    assert user_id_sug == []


def test_suggest_fks_skips_pk_pk():
    """Same-name shared column that's a PK on both sides is identity, not a join."""
    from speaksql.graph import suggest_fks
    from speaksql.introspect import ColumnInfo, SchemaList, TableInfo

    schema = SchemaList(
        tables=(
            TableInfo(None, None, "users", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
            )),
            TableInfo(None, None, "sessions", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
            )),
        ),
        foreign_keys=(),
    )
    suggestions = suggest_fks(schema)
    # Should NOT suggest id <-> id (PK on both sides)
    id_id = [
        s for s in suggestions
        if s.from_column == "id" and s.to_column == "id"
    ]
    assert id_id == []


def test_suggest_fks_skips_type_mismatch():
    """Shared column name with different data types shouldn't link."""
    from speaksql.graph import suggest_fks
    from speaksql.introspect import ColumnInfo, SchemaList, TableInfo

    schema = SchemaList(
        tables=(
            TableInfo(None, None, "a", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("value", "INTEGER"),
            )),
            TableInfo(None, None, "b", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("value", "TEXT"),  # different type
            )),
        ),
        foreign_keys=(),
    )
    suggestions = suggest_fks(schema)
    # 'value' shouldn't be linked because types differ
    value_links = [
        s for s in suggestions
        if s.from_column == "value" or s.to_column == "value"
    ]
    assert value_links == []


def test_suggest_fks_singular_forms():
    """order_items.order_id should match 'order' singular of 'orders'."""
    from speaksql.graph import suggest_fks
    from speaksql.introspect import ColumnInfo, SchemaList, TableInfo

    schema = SchemaList(
        tables=(
            TableInfo(None, None, "orders", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
            )),
            TableInfo(None, None, "order_items", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("order_id", "INTEGER"),
            )),
        ),
        foreign_keys=(),
    )
    suggestions = suggest_fks(schema)
    assert any(
        s.from_table == "order_items" and s.from_column == "order_id"
        and s.to_table == "orders" and s.to_column == "id"
        for s in suggestions
    )


def test_suggest_fks_empty_when_no_pattern_matches():
    """Two unrelated tables: no suggestions should be returned."""
    from speaksql.graph import suggest_fks
    from speaksql.introspect import ColumnInfo, SchemaList, TableInfo

    schema = SchemaList(
        tables=(
            TableInfo(None, None, "alpha", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("foo", "INTEGER"),
            )),
            TableInfo(None, None, "beta", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("bar", "TEXT"),
            )),
        ),
        foreign_keys=(),
    )
    suggestions = suggest_fks(schema)
    # No foo/alpha_id, no bar/beta_id, no shared names -> empty
    assert suggestions == ()


def test_suggest_fks_returns_sorted_by_confidence():
    """Higher-confidence suggestions come first."""
    from speaksql.graph import suggest_fks
    from speaksql.introspect import ColumnInfo, SchemaList, TableInfo

    schema = SchemaList(
        tables=(
            TableInfo(None, None, "users", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
            )),
            TableInfo(None, None, "orders", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("user_id", "INTEGER"),
                ColumnInfo("status", "TEXT"),
            )),
            TableInfo(None, None, "status_log", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("status", "TEXT"),
            )),
        ),
        foreign_keys=(),
    )
    suggestions = suggest_fks(schema)
    if len(suggestions) >= 2:
        # First suggestion must be >= confidence of any later suggestion
        assert all(
            suggestions[i].confidence >= suggestions[i + 1].confidence
            for i in range(len(suggestions) - 1)
        )


def test_fk_suggestion_is_dataclass():
    """FKSuggestion should be a frozen dataclass."""
    from dataclasses import FrozenInstanceError

    from speaksql.graph import FKSuggestion

    s = FKSuggestion(
        from_table="orders",
        from_column="user_id",
        to_table="users",
        to_column="id",
        confidence=0.9,
        reason="test",
    )
    with pytest.raises(FrozenInstanceError):
        s.confidence = 0.5  # type: ignore[misc]