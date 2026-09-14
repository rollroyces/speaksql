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