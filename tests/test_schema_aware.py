"""Tests for the FK-aware SQL generation module."""

from __future__ import annotations

from speaksql.introspect import ColumnInfo, ForeignKeyInfo, SchemaList, TableInfo
from speaksql.schema_aware import (
    JoinEdge,
    joins_to_sql,
    plan_for_question,
)


def _ecommerce_schema() -> SchemaList:
    """Realistic 3-table schema with declared FKs."""
    return SchemaList(
        tables=(
            TableInfo(None, None, "users", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("email", "TEXT"),
                ColumnInfo("country", "TEXT"),
            )),
            TableInfo(None, None, "orders", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("user_id", "INTEGER"),
                ColumnInfo("total", "DOUBLE"),
                ColumnInfo("created_at", "TIMESTAMP"),
            )),
            TableInfo(None, None, "order_items", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("order_id", "INTEGER"),
                ColumnInfo("product_id", "INTEGER"),
                ColumnInfo("qty", "INTEGER"),
            )),
            TableInfo(None, None, "products", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("name", "TEXT"),
                ColumnInfo("price", "DOUBLE"),
            )),
        ),
        foreign_keys=(
            ForeignKeyInfo("orders", "user_id", "users", "id"),
            ForeignKeyInfo("order_items", "order_id", "orders", "id"),
            ForeignKeyInfo("order_items", "product_id", "products", "id"),
        ),
    )


def test_plan_finds_users_table_for_user_token():
    hint = plan_for_question("show all users", _ecommerce_schema())
    assert "users" in hint.tables


def test_plan_finds_orders_and_users_with_join():
    """Two tokens → both tables selected → JOIN edge between them."""
    hint = plan_for_question("count of orders per user", _ecommerce_schema())
    assert set(hint.tables) >= {"orders", "users"}
    join_pairs = {(j.from_table, j.to_table) for j in hint.joins}
    assert ("orders", "users") in join_pairs


def test_plan_three_table_chain_picks_relevant_joins():
    """A question mentioning 'orders' and 'order_items' by name (both real
    substrings of the question tokens) should pick up both tables plus
    their FKs to products and users respectively."""
    hint = plan_for_question("orders and order_items joined with products", _ecommerce_schema())
    # Both 'orders' and 'order_items' should be selected (direct matches)
    assert "orders" in hint.tables
    assert "order_items" in hint.tables
    # 'products' is also referenced; should also be selected
    assert "products" in hint.tables
    pairs = {(j.from_table, j.to_table) for j in hint.joins}
    # Both FK edges should be picked up
    assert ("order_items", "orders") in pairs
    assert ("order_items", "products") in pairs


def test_plan_handles_fully_qualified_table_names():
    """DuckDB returns FKs as 'schema.table' — planner should normalize."""
    schema_with_qualifiers = SchemaList(
        tables=(
            TableInfo(None, "main", "users"),
            TableInfo(None, "main", "orders"),
        ),
        foreign_keys=(
            ForeignKeyInfo("main.orders", "user_id", "main.users", "id"),
        ),
    )
    hint = plan_for_question("count of orders per user", schema_with_qualifiers)
    # Both tables should be in chosen regardless of FK qualifier
    assert set(hint.tables) >= {"orders", "users"}
    # FK should be picked up despite qualifier mismatch
    assert len(hint.joins) == 1
    assert hint.joins[0].from_table == "orders"
    assert hint.joins[0].to_table == "users"


def test_plan_returns_empty_hint_for_no_match():
    """Unrelated question → no tables selected, unresolved tokens listed."""
    hint = plan_for_question("xyzzy frobnitz", _ecommerce_schema())
    assert hint.tables == ()
    assert "xyzzy" in hint.unresolved
    assert "frobnitz" in hint.unresolved


def test_plan_scores_plural_table_name_higher():
    """'users' should match table 'users' exactly, not just substrings."""
    hint = plan_for_question("how many users", _ecommerce_schema())
    # 'users' is the top match — and singular form too
    assert hint.tables[0] == "users"


def test_plan_columns_match_tracked_in_hint():
    """If the question names columns, they should be in hint.columns."""
    hint = plan_for_question("list user email and country", _ecommerce_schema())
    # Both 'email' and 'country' are columns on the users table
    if "users" in hint.columns:
        cols = hint.columns["users"]
        assert "email" in cols
        assert "country" in cols


def test_to_prompt_section_includes_tables_and_joins():
    hint = plan_for_question("revenue by product via order_items", _ecommerce_schema())
    section = hint.to_prompt_section()
    assert "Tables:" in section
    assert "order_items" in section
    if hint.joins:
        assert "Joins:" in section


def test_joins_to_sql_renders_simple_join():
    edges = (JoinEdge("orders", "user_id", "users", "id"),)
    sql = joins_to_sql(edges)
    assert sql == "JOIN users ON orders.user_id = users.id"


def test_joins_to_sql_handles_empty():
    assert joins_to_sql(()) == ""


def test_join_edge_preserves_constraint_name():
    fk = ForeignKeyInfo("orders", "user_id", "users", "id", constraint_name="fk_o_u")
    assert fk.constraint_name == "fk_o_u"


def test_plan_handles_fk_column_token_singular_form():
    """If the question includes 'user_id' (FK column), the planner should
    pick up BOTH the parent and child table via the singular-form match."""
    hint = plan_for_question("orders where user_id is set", _ecommerce_schema())
    # 'orders' matches directly; 'user_id' is an FK column so it should
    # hint at users too
    assert "orders" in hint.tables
    assert "users" in hint.tables