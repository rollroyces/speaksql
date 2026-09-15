"""BigQuery backend via google-cloud-bigquery.

    pip install speaksql[bigquery]

Auth is handled by google-cloud-bigquery via Application Default Credentials —
set GOOGLE_APPLICATION_CREDENTIALS or run `gcloud auth application-default
login`. Pass `project=` to override the inferred project, or supply a
fully-built `Client` via the `client=` kwarg.
"""

from __future__ import annotations

from typing import Any

from speaksql.introspect import ColumnInfo, SchemaList, TableInfo


class BigQueryBackend:
    """Google BigQuery backend (google-cloud-bigquery driver)."""

    name = "bigquery"

    def __init__(
        self,
        project: str | None = None,
        client: Any | None = None,
        location: str | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=project, location=location)

    def introspect(self) -> SchemaList:
        datasets = list(self._client.list_datasets())
        tables: list[TableInfo] = []
        for ds in datasets:
            for table in self._client.list_tables(ds.reference):
                tbl_ref = self._client.get_table(table.reference)
                cols = tuple(
                    ColumnInfo(
                        name=field.name,
                        data_type=field.field_type,
                        nullable=field.is_nullable,
                        is_primary_key=(field.name in (tbl_ref.primary_key or [])),
                    )
                    for field in tbl_ref.schema
                )
                # Split dataset.table into schema + name so the SchemaList
                # shape matches the other backends.
                schema_name = table.dataset_id or ""
                tables.append(
                    TableInfo(
                        catalog=table.project,
                        schema=schema_name,
                        name=table.table_id,
                        columns=cols,
                    )
                )
        return SchemaList(tables=tuple(tables))

    def execute(self, sql: str) -> list[tuple]:
        job = self._client.query(sql)
        result = job.result()
        if result.schema is None:
            return []
        return [tuple(row.values()) for row in result]

    def foreign_keys(self) -> tuple:
        """BigQuery does not support FK constraints in DDL — always empty.

        Returned as a no-op for protocol completeness. Schema linking
        for BigQuery will rely on the name-based JOIN heuristic instead.
        """
        return ()

    def close(self) -> None:
        self._client.close()