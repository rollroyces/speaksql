"""Databricks / Spark backend via PySpark.

    pip install speaksql[spark]

PySpark connects via `SparkSession.builder` with a configured `master`.
Common setups:

Local mode (in-process Spark, useful for tests):

    backend = SparkBackend(master="local[*]", app_name="speaksql")

Existing Spark cluster:

    backend = SparkBackend(
        master="spark://spark-master:7077",
        app_name="speaksql",
    )

Databricks Connect (the recommended way to talk to a Databricks
SQL warehouse from outside the cluster):

    backend = SparkBackend(
        master="sc://<workspace>.databricks.com:443/;token=<pat>",
        app_name="speaksql",
    )

Note: PySpark requires a JVM and is a heavyweight dependency. Only
install when you actually need to talk to a Spark/Databricks cluster.
"""

from __future__ import annotations

from typing import Any

from speaksql.introspect import ColumnInfo, ForeignKeyInfo, SchemaList, TableInfo


class SparkBackend:
    """Databricks / Spark backend (PySpark driver)."""

    name = "spark"

    def __init__(self, **kwargs: Any) -> None:
        from pyspark.sql import SparkSession

        # SparkSession.builder is a fluent API; the user passes
        # .config(...) options via `spark_options={"spark.sql.warehouse.dir": ...}`
        # if needed, plus the required `master` and `appName`.
        master = kwargs.pop("master", "local[*]")
        app_name = kwargs.pop("app_name", "speaksql")
        spark_options = kwargs.pop("spark_options", None) or {}

        builder = SparkSession.builder.master(master).appName(app_name)
        for k, v in spark_options.items():
            builder = builder.config(k, v)

        self._session = builder.getOrCreate()
        self._spark = self._session.sparkContext  # for stop()

    def introspect(self) -> SchemaList:
        """Read tables + columns from the catalog."""
        catalog = self._session.catalog
        tables_meta = catalog.listTables()
        tables: list[TableInfo] = []
        for t in tables_meta:
            name = t.name
            database = t.database or "default"
            try:
                cols = self._session.catalog.listColumns(name, dbName=database)
            except (RuntimeError, AttributeError, ValueError):
                # Some catalogs don't support listColumns — fall back to
                # an empty column list rather than crashing.
                cols = []
            columns = tuple(
                ColumnInfo(
                    name=c.name,
                    data_type=c.dataType,
                    nullable=c.nullable,
                    is_primary_key=False,  # Spark catalog doesn't expose PKs
                )
                for c in cols
            )
            tables.append(
                TableInfo(
                    catalog=None,
                    schema=database,
                    name=name,
                    columns=columns,
                )
            )
        return SchemaList(tables=tuple(tables))

    def execute(self, sql: str) -> list[tuple]:
        """Execute a SQL query and return rows as plain tuples."""
        df = self._session.sql(sql)
        rows = df.collect()
        if not rows:
            return []
        # Spark Row objects support field indexing like tuples.
        return [tuple(r) for r in rows]

    def foreign_keys(self) -> tuple[ForeignKeyInfo, ...]:
        """Spark 3.x does not expose FK metadata via the catalog API.

        Always returns an empty tuple. Use the schema-aware JOIN graph
        heuristic to infer FKs from column-name conventions if needed.
        """
        return ()

    def close(self) -> None:
        # Stopping the SparkSession would tear down the JVM — which would
        # also kill any other SparkBackend in the same process. We avoid
        # that by leaving the session running. Callers who want a hard
        # stop can do `backend._spark.stop()` explicitly.
        pass