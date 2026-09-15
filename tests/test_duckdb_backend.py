"""Live roundtrip tests against the DuckDB backend.

Skipped if the `duckdb` extra isn't installed.
"""

from __future__ import annotations

import pytest
import speaksql
from speaksql.exceptions import BackendError

pytestmark = pytest.mark.skipif(
    not pytest.importorskip("duckdb", reason="duckdb not installed"),
    reason="duckdb driver not installed",
)


def test_duckdb_introspect(duckdb_backend):
    schema = duckdb_backend.introspect()
    names = {t.name for t in schema.tables}
    assert "events" in names

    events = next(t for t in schema.tables if t.name == "events")
    id_col = next(c for c in events.columns if c.name == "id")
    assert id_col.is_primary_key is True
    country_col = next(c for c in events.columns if c.name == "country")
    assert country_col.nullable is False


def test_duckdb_execute_select(duckdb_backend):
    rows = duckdb_backend.execute("SELECT COUNT(*) FROM events")
    assert rows == [(5,)]


def test_duckdb_full_roundtrip(duckdb_backend):
    """Transpile a canonical query, then execute the dialect-flavored output."""
    canonical = (
        "SELECT DATE_TRUNC('month', created_at) AS month, country, SUM(amount) AS total "
        "FROM events GROUP BY month, country ORDER BY month, total DESC"
    )
    out = speaksql.transpile(canonical, targets=("duckdb",))
    rows = duckdb_backend.execute(out["duckdb"])
    assert len(rows) == 5  # 5 (month, country) combinations
    # Ordered by month asc, total desc — first row should be Jan 2026, US
    assert rows[0][1] == "US"
    assert rows[0][2] == 150.0
    # Last row should be March 2026, CN
    assert rows[-1][1] == "CN"
    assert rows[-1][2] == 300.0


def test_duckdb_backend_for_constructor():
    from speaksql.backends import backend_for

    be = backend_for("duckdb")
    try:
        assert be.execute("SELECT 42 AS x") == [(42,)]
    finally:
        be.close()


def test_duckdb_close_is_safe(duckdb_backend):
    # Closing twice should not raise
    duckdb_backend.close()
    try:
        duckdb_backend.close()
    except OSError as e:
        pytest.fail(f"double-close raised OSError: {e}")


def test_duckdb_missing_driver_message(monkeypatch):
    """If the duckdb module is missing, backend_for surfaces a clean hint."""
    import builtins

    from speaksql.backends import backend_for

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "duckdb" or name.startswith("duckdb."):
            raise ImportError("simulated: duckdb not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BackendError) as exc:
        backend_for("duckdb")
    assert "duckdb" in str(exc.value).lower()