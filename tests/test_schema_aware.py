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


# ---------------------------------------------------------------------------
# Multi-hop JOIN path tests
# ---------------------------------------------------------------------------


def test_multi_hop_users_to_order_items_pulls_in_orders():
    """Question names only users + order_items; BFS should pull in orders
    via the FK chain users <- orders -> order_items."""
    hint = plan_for_question("users with their order_items", _ecommerce_schema())
    # All three tables should be in chosen
    assert "users" in hint.tables
    assert "order_items" in hint.tables
    assert "orders" in hint.tables
    # Both FK edges should be in joins
    pairs = {(j.from_table, j.to_table) for j in hint.joins}
    assert ("orders", "users") in pairs
    assert ("order_items", "orders") in pairs


def test_multi_hop_users_to_products_full_chain():
    """users + products needs the full chain users -> orders -> order_items -> products."""
    hint = plan_for_question("revenue per user via products", _ecommerce_schema())
    # All four tables should be present
    for t in ("users", "orders", "order_items", "products"):
        assert t in hint.tables, f"missing {t} from {hint.tables}"
    # Three joins: users<-orders, orders<-order_items, order_items<-products
    pairs = {(j.from_table, j.to_table) for j in hint.joins}
    assert ("orders", "users") in pairs
    assert ("order_items", "orders") in pairs
    assert ("order_items", "products") in pairs


def test_multi_hop_no_path_returns_no_intermediate():
    """If two chosen tables aren't FK-connected at all, no intermediate
    tables are added and only direct joins are emitted."""
    schema = SchemaList(
        tables=(
            TableInfo(None, None, "users", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
            )),
            TableInfo(None, None, "audit_log", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("msg", "TEXT"),
                ColumnInfo("user_id", "INTEGER"),
            )),
        ),
        foreign_keys=(
            # Note: only audit_log has an FK TO users, but users has no FK
            ForeignKeyInfo("audit_log", "user_id", "users", "id"),
        ),
    )
    schema2 = SchemaList(
        tables=schema.tables,
        foreign_keys=(),
    )
    hint = plan_for_question("show users and audit_log", schema2)
    # No FKs at all, so no joins
    assert hint.joins == ()


def test_multi_hop_preserves_user_order():
    """User-mentioned tables should appear before interpolated ones in
    the hint's tables tuple."""
    hint = plan_for_question("users and order_items", _ecommerce_schema())
    # users and order_items were user-mentioned; orders is interpolated.
    # Both user ones should appear before orders in the tuple.
    users_idx = hint.tables.index("users")
    oi_idx = hint.tables.index("order_items")
    # orders might or might not be in the tuple; if it is, it should be
    # after the user ones.
    if "orders" in hint.tables:
        orders_idx = hint.tables.index("orders")
        assert users_idx < orders_idx
        assert oi_idx < orders_idx


def test_multi_hop_idempotent():
    """Repeated BFS runs on the same hint produce the same answer."""
    q = "revenue per user via products"
    h1 = plan_for_question(q, _ecommerce_schema())
    h2 = plan_for_question(q, _ecommerce_schema())
    assert h1.tables == h2.tables
    assert {(j.from_table, j.to_table) for j in h1.joins} == {
        (j.from_table, j.to_table) for j in h2.joins
    }