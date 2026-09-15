"""Tests for the Spark / Databricks backend.

We can't run a live Spark JVM in CI (it would require OpenJDK
install + several minutes of startup), so these tests exercise the
backend with mock SparkSession + catalog objects. The real driver is
covered by smoke tests in `examples/spark_smoke.py` for users who have
a Spark cluster.
"""

from __future__ import annotations

import pytest
from speaksql.introspect import SchemaList


def test_spark_backend_class_loads():
    from speaksql.backends.spark_backend import SparkBackend

    assert SparkBackend.name == "spark"


def test_spark_backend_aliases_resolve_through_factory(monkeypatch):
    """`spark` and `databricks` both resolve to SparkBackend."""
    from speaksql.backends import backend_for

    captured = {}

    class FakeBackend:
        name = "spark"

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

        def introspect(self):
            return SchemaList(tables=())

        def execute(self, sql):
            return []

        def foreign_keys(self):
            return ()

    monkeypatch.setattr("speaksql.backends.spark_backend.SparkBackend", FakeBackend)
    for spelling in ("spark", "databricks"):
        be = backend_for(spelling, master="local[*]", app_name="x")
        assert be.name == "spark"
        assert captured["master"] == "local[*]"
        be.close()


def test_spark_backend_requires_pyspark(monkeypatch):
    """If pyspark isn't installed, backend_for surfaces BackendError."""
    import builtins

    from speaksql.backends import backend_for
    from speaksql.exceptions import BackendError

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pyspark" or name.startswith("pyspark."):
            raise ImportError("simulated missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BackendError) as exc:
        backend_for("spark")
    assert "pyspark" in str(exc.value).lower()


def test_spark_introspect_uses_catalog_listtables(monkeypatch):
    """SparkBackend.introspect() should query the catalog's listTables()."""
    from speaksql.backends.spark_backend import SparkBackend

    class FakeColumn:
        def __init__(self, name, data_type, nullable):
            self.name = name
            self.dataType = data_type
            self.nullable = nullable

    class FakeTable:
        def __init__(self, name, database):
            self.name = name
            self.database = database

    class FakeCatalog:
        def __init__(self):
            self.calls = []

        def listTables(self):
            self.calls.append("listTables")
            return [FakeTable("events", "analytics")]

        def listColumns(self, name, dbName=None):
            self.calls.append(("listColumns", name, dbName))
            return [FakeColumn("id", "int", True), FakeColumn("amount", "double", False)]

    class FakeSparkContext:
        def stop(self):
            pass

    class FakeSession:
        def __init__(self):
            self.catalog = FakeCatalog()
            self.sparkContext = FakeSparkContext()

    be = SparkBackend.__new__(SparkBackend)
    be._session = FakeSession()  # type: ignore[attr-defined]
    try:
        schema = be.introspect()
        assert len(schema.tables) == 1
        t = schema.tables[0]
        assert t.name == "events"
        assert t.schema == "analytics"
        assert len(t.columns) == 2
        assert t.columns[0].name == "id"
    finally:
        be.close()


def test_spark_introspect_handles_catalog_failure(monkeypatch):
    """If listColumns() raises, introspect should swallow the error and
    return an empty ColumnInfo for that table — better than crashing."""
    from speaksql.backends.spark_backend import SparkBackend

    class FakeTable:
        def __init__(self, name, database):
            self.name = name
            self.database = database

    class FakeCatalog:
        def listTables(self):
            return [FakeTable("broken", "default")]

        def listColumns(self, name, dbName=None):
            raise RuntimeError("catalog unavailable")

    class FakeSession:
        def __init__(self):
            self.catalog = FakeCatalog()

    be = SparkBackend.__new__(SparkBackend)
    be._session = FakeSession()  # type: ignore[attr-defined]
    try:
        schema = be.introspect()
        assert len(schema.tables) == 1
        assert schema.tables[0].columns == ()
    finally:
        be.close()


def test_spark_execute_returns_row_tuples(monkeypatch):
    """execute() should convert Spark Row objects to plain tuples."""
    from speaksql.backends.spark_backend import SparkBackend

    class FakeRow:
        def __init__(self, values):
            self._values = values
        def __getitem__(self, idx):
            return self._values[idx]

    class FakeDataFrame:
        def collect(self):
            return [FakeRow((1, 100.0)), FakeRow((2, 200.0))]

    class FakeSession:
        def __init__(self):
            self.executed = []
        def sql(self, query):
            self.executed.append(query)
            return FakeDataFrame()

    be = SparkBackend.__new__(SparkBackend)
    session = FakeSession()
    be._session = session  # type: ignore[attr-defined]
    try:
        rows = be.execute("SELECT id, amount FROM events")
        assert rows == [(1, 100.0), (2, 200.0)]
        assert session.executed == ["SELECT id, amount FROM events"]
    finally:
        be.close()


def test_spark_foreign_keys_always_empty(monkeypatch):
    """Spark 3.x/4.x doesn't expose FKs via the catalog API."""
    from speaksql.backends.spark_backend import SparkBackend

    be = SparkBackend.__new__(SparkBackend)
    be._session = None  # type: ignore[attr-defined]
    try:
        assert be.foreign_keys() == ()
    finally:
        be.close()


def test_spark_join_graph_falls_back_to_heuristic():
    """Spark has no FK metadata, so build_join_graph uses the heuristic.
    Verify the round-trip: schema → graph → expected heuristic edges."""
    from speaksql.graph import build_join_graph
    from speaksql.introspect import ColumnInfo, TableInfo

    schema = SchemaList(
        tables=(
            TableInfo(None, None, "orders", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("user_id", "INTEGER"),
            )),
            TableInfo(None, None, "users", columns=(
                ColumnInfo("id", "INTEGER", is_primary_key=True),
                ColumnInfo("email", "TEXT"),
            )),
        ),
    )
    g = build_join_graph(schema)
    pairs = {(e.source, e.target) for e in g.edges}
    assert ("orders", "users") in pairs  # heuristic picks up user_id → id