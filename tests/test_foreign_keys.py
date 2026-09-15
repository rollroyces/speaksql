"""Tests for foreign key detection across backends."""

from __future__ import annotations

import sqlite3

import pytest
from speaksql.backends.sqlite_backend import SQLiteBackend
from speaksql.introspect import ColumnInfo, ForeignKeyInfo, SchemaList, TableInfo


def test_foreign_key_info_dataclass():
    fk = ForeignKeyInfo(
        from_table="orders",
        from_column="user_id",
        to_table="users",
        to_column="id",
    )
    assert fk.from_table == "orders"
    assert fk.from_column == "user_id"
    assert fk.to_table == "users"
    assert fk.to_column == "id"
    assert fk.constraint_name is None


def test_schema_list_with_foreign_keys():
    fk = ForeignKeyInfo("a", "b", "c", "d")
    base = SchemaList(tables=())
    enriched = base.with_foreign_keys((fk,))
    assert enriched.foreign_keys == (fk,)


def test_sqlite_introspect_attaches_no_fks():
    """introspect() alone returns no FKs; callers should use foreign_keys()."""
    be = SQLiteBackend()
    try:
        be._conn.execute("CREATE TABLE u (id INTEGER PRIMARY KEY)")
        schema = be.introspect()
        # SchemaList.foreign_keys defaults to empty (callers attach via
        # with_foreign_keys()).
        assert schema.foreign_keys == ()
    finally:
        be.close()


def test_sqlite_foreign_keys_classic_schema(tmp_path):
    db = tmp_path / "fk.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    con.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, "
        "user_id INTEGER REFERENCES users(id))"
    )
    con.execute(
        "CREATE TABLE order_items (id INTEGER PRIMARY KEY, "
        "order_id INTEGER REFERENCES orders(id))"
    )
    con.commit()
    con.close()

    be = SQLiteBackend(path=str(db))
    try:
        fks = be.foreign_keys()
        pairs = {(fk.from_table, fk.to_table) for fk in fks}
        assert ("orders", "users") in pairs
        assert ("order_items", "orders") in pairs
        # Columns are correctly captured
        for fk in fks:
            if fk.from_table == "orders":
                assert fk.from_column == "user_id"
                assert fk.to_column == "id"
            if fk.from_table == "order_items":
                assert fk.from_column == "order_id"
                assert fk.to_column == "id"
    finally:
        be.close()


def test_sqlite_no_foreign_keys_when_undeclared(tmp_path):
    db = tmp_path / "no_fk.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE t1 (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE t2 (id INTEGER PRIMARY KEY, t1_id INTEGER)")
    con.commit()
    con.close()

    be = SQLiteBackend(path=str(db))
    try:
        fks = be.foreign_keys()
        # t2.t1_id is named like an FK but no REFERENCES clause was given.
        assert fks == ()
    finally:
        be.close()


def test_graph_uses_real_fks_over_heuristic():
    """When schema carries real FKs, the graph uses them regardless of
    whether the heuristic would also have found the same edges."""
    tables = (
        TableInfo(
            catalog=None,
            schema=None,
            name="users",
            columns=(
                ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),
                ColumnInfo("email", "TEXT"),
            ),
        ),
        TableInfo(
            catalog=None,
            schema=None,
            name="orders",
            columns=(
                ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),
                ColumnInfo("user_id", "INTEGER"),
            ),
        ),
        TableInfo(
            catalog=None,
            schema=None,
            name="accidentally_matching_table",
            columns=(ColumnInfo("id", "INTEGER", nullable=False, is_primary_key=True),),
        ),
    )
    # Real FK only declares orders.user_id -> users.id
    fks = (
        ForeignKeyInfo(
            from_table="orders",
            from_column="user_id",
            to_table="users",
            to_column="id",
        ),
    )
    schema = SchemaList(tables=tables, foreign_keys=fks)

    from speaksql.graph import build_join_graph

    g = build_join_graph(schema)
    pairs = {frozenset((e.source, e.target)) for e in g.edges}
    assert frozenset(("orders", "users")) in pairs
    # Heuristic would also have matched accidentally_matching_table ↔ users
    # via PK↔PK, but real-FK path skips it (different column name requirement
    # doesn't apply, BUT there's no FK for it either). Verify only the
    # declared FK shows up.
    assert frozenset(("accidentally_matching_table", "users")) not in pairs


def test_graph_falls_back_to_heuristic_when_no_fks():
    """Empty foreign_keys → name-based heuristic kicks in."""
    from speaksql.graph import build_join_graph

    schema = SchemaList(
        tables=(
            TableInfo(
                catalog=None,
                schema=None,
                name="users",
                columns=(ColumnInfo("id", "INTEGER", is_primary_key=True),),
            ),
            TableInfo(
                catalog=None,
                schema=None,
                name="posts",
                columns=(
                    ColumnInfo("id", "INTEGER", is_primary_key=True),
                    ColumnInfo("user_id", "INTEGER"),
                ),
            ),
        ),
        foreign_keys=(),  # explicit
    )
    g = build_join_graph(schema)
    pairs = {frozenset((e.source, e.target)) for e in g.edges}
    assert frozenset(("users", "posts")) in pairs


def test_graph_use_real_fks_false_forces_heuristic():
    """Caller can opt out of real FKs to test the heuristic."""
    from speaksql.graph import build_join_graph

    schema = SchemaList(
        tables=(
            TableInfo(
                catalog=None,
                schema=None,
                name="users",
                columns=(ColumnInfo("id", "INTEGER", is_primary_key=True),),
            ),
            TableInfo(
                catalog=None,
                schema=None,
                name="posts",
                columns=(
                    ColumnInfo("id", "INTEGER", is_primary_key=True),
                    ColumnInfo("user_id", "INTEGER"),
                ),
            ),
        ),
        foreign_keys=(
            ForeignKeyInfo(
                from_table="nonexistent",
                from_column="id",
                to_table="users",
                to_column="id",
            ),
        ),
    )
    g_heuristic = build_join_graph(schema, use_real_fks=False)
    # Heuristic still finds the real users<->posts FK
    pairs = {frozenset((e.source, e.target)) for e in g_heuristic.edges}
    assert frozenset(("users", "posts")) in pairs


# DuckDB tests — only run if driver installed
duckdb = pytest.importorskip("duckdb")


def test_duckdb_foreign_keys_classic_schema():
    """Live DuckDB FK detection via duckdb_constraints()."""
    from speaksql.backends.duckdb_backend import DuckDBBackend

    be = DuckDBBackend()
    try:
        be.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        be.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)")
        be.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, "
            "user_id INTEGER REFERENCES users(id))"
        )
        be.execute(
            "CREATE TABLE order_items ("
            "  id INTEGER PRIMARY KEY, "
            "  order_id INTEGER REFERENCES orders(id), "
            "  product_id INTEGER REFERENCES products(id)"
            ")"
        )
        fks = be.foreign_keys()
        # DuckDB returns tables as 'main.<name>' — check that all 4 FKs
        # are present by their short name.
        edges_by_short = {
            (fk.from_table.split(".")[-1], fk.to_table.split(".")[-1])
            for fk in fks
        }
        assert ("orders", "users") in edges_by_short
        assert ("order_items", "orders") in edges_by_short
        assert ("order_items", "products") in edges_by_short
    finally:
        be.close()


def test_duckdb_no_foreign_keys_returns_empty():
    from speaksql.backends.duckdb_backend import DuckDBBackend

    be = DuckDBBackend()
    try:
        be.execute("CREATE TABLE t1 (id INTEGER PRIMARY KEY)")
        be.execute("CREATE TABLE t2 (id INTEGER PRIMARY KEY, t1_id INTEGER)")
        assert be.foreign_keys() == ()
    finally:
        be.close()


def test_duckdb_foreign_keys_group_multi_column_pairs():
    """Multiple FKs from the same child table to the same parent should
    produce a single edge listing all FK columns."""
    from speaksql.backends.duckdb_backend import DuckDBBackend

    be = DuckDBBackend()
    try:
        be.execute(
            "CREATE TABLE a (x INTEGER PRIMARY KEY, y INTEGER UNIQUE)"
        )
        # Two FKs from link -> a (different columns on both sides)
        be.execute(
            "CREATE TABLE link ("
            "  p INTEGER REFERENCES a(x), "
            "  q INTEGER REFERENCES a(y)"
            ")"
        )
        fks = be.foreign_keys()
        # link should have 2 FKs to a
        link_fks = [fk for fk in fks if fk.from_table.endswith("link")]
        assert len(link_fks) == 2
        from_cols = {fk.from_column for fk in link_fks}
        assert from_cols == {"p", "q"}
    finally:
        be.close()