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


def test_service_diff_identical():
    from fastapi.testclient import TestClient
    from speaksql.service import app

    client = TestClient(app)
    r = client.post(
        "/v1/diff",
        json={"sql_a": "SELECT id FROM t", "sql_b": "SELECT id FROM t"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["identical"] is True
    assert body["differences"] == []


def test_service_diff_detects_null_ordering():
    from fastapi.testclient import TestClient
    from speaksql.service import app

    client = TestClient(app)
    r = client.post(
        "/v1/diff",
        json={
            "sql_a": "SELECT id FROM t ORDER BY id ASC NULLS LAST",
            "sql_b": "SELECT id FROM t ORDER BY id ASC",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["identical"] is False
    assert any(d["category"] == "null_ordering" for d in body["differences"])


def test_service_graph_sqlite(tmp_path):
    """POST /v1/graph with a seeded SQLite file."""
    import sqlite3

    db = tmp_path / "svc.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER)")
    con.commit()
    con.close()

    from fastapi.testclient import TestClient
    from speaksql.service import app

    client = TestClient(app)
    r = client.post(
        "/v1/graph",
        json={"db_path": str(db), "backend": "sqlite"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["nodes"] == 2
    assert body["edges"] == 1
    assert "<!doctype html>" in body["html"].lower()
    assert "users" in body["graph"]["nodes"][0]["name"] or any(
        n["name"] == "users" for n in body["graph"]["nodes"]
    )


def test_service_graph_rejects_bad_backend(tmp_path):
    import sqlite3

    db = tmp_path / "x.sqlite"
    sqlite3.connect(str(db)).close()

    from fastapi.testclient import TestClient
    from speaksql.service import app

    client = TestClient(app)
    r = client.post(
        "/v1/graph",
        json={"db_path": str(db), "backend": "oracle"},
    )
    assert r.status_code == 400


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