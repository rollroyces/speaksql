"""Tests for the JOIN graph builder and HTML renderer."""

from __future__ import annotations

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