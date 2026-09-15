"""Service tests (skipped if fastapi not installed)."""

import pytest

pytestmark = pytest.mark.service


def test_service_dialects_endpoint():
    from fastapi.testclient import TestClient
    from speaksql.service import app

    client = TestClient(app)
    r = client.get("/v1/dialects")
    assert r.status_code == 200
    body = r.json()
    assert "postgres" in body["supported"]
    assert "snowflake" in body["supported"]


def test_service_ask_endpoint():
    from fastapi.testclient import TestClient
    from speaksql.service import app

    client = TestClient(app)
    r = client.post(
        "/v1/ask",
        json={"question": "SELECT 1 AS x", "is_sql": True, "dialects": ["postgres", "snowflake"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert "postgres" in body["results"]
    assert "snowflake" in body["results"]


def test_service_ask_rejects_bad_dialect():
    from fastapi.testclient import TestClient
    from speaksql.service import app

    client = TestClient(app)
    r = client.post(
        "/v1/ask",
        json={"question": "SELECT 1", "is_sql": True, "dialects": ["oracle"]},
    )
    assert r.status_code == 400


def test_service_execute_duckdb(tmp_path):
    """Live execute path against a seeded DuckDB file."""
    import duckdb
    from fastapi.testclient import TestClient
    from speaksql.service import app

    db = tmp_path / "events.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE events (id INTEGER, country TEXT, amount DOUBLE, created_at TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO events VALUES "
        "(1, 'JP', 100.0, '2026-01-15'),"
        "(2, 'US', 150.0, '2026-02-20')"
    )
    con.close()

    client = TestClient(app)
    r = client.post(
        "/v1/execute",
        json={
            "question": (
                "SELECT DATE_TRUNC('month', created_at) AS m, "
                "SUM(amount) AS total FROM events GROUP BY m ORDER BY m"
            ),
            "is_sql": True,
            "backend": "duckdb",
            "db_path": str(db),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["executed"] is True
    assert body["backend"] == "duckdb"
    assert body["row_count"] == 2
    # rows are serialised to ISO datetime + numeric
    assert body["rows"][0][0].startswith("2026-")


def test_service_execute_missing_driver_400():
    """If the driver can't construct (missing creds or missing driver), surface a reason.

    We can't easily simulate "driver missing" without monkeypatching, so
    instead we exercise the snowflake path (which fails on config manager
    without credentials) and verify the service returns executed=False
    with a non-empty reason rather than 500-ing.
    """
    from fastapi.testclient import TestClient
    from speaksql.service import app

    client = TestClient(app)
    r = client.post(
        "/v1/execute",
        json={
            "question": "SELECT 1",
            "is_sql": True,
            "backend": "snowflake",
        },
    )
    assert r.status_code == 200
    body = r.json()
    # Either: snowflake driver isn't installed → BackendError-style reason
    # Or: snowflake driver IS installed → construct fails on empty creds → reason
    assert body["executed"] is False
    assert body.get("reason") is not None