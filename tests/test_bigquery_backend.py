"""Smoke tests for the BigQuery backend constructor."""

from __future__ import annotations

import pytest
from speaksql.exceptions import BackendError


def test_bigquery_constructor_class_exists():
    from speaksql.backends.bigquery_backend import BigQueryBackend

    assert hasattr(BigQueryBackend, "introspect")
    assert hasattr(BigQueryBackend, "execute")
    assert hasattr(BigQueryBackend, "close")


def test_bigquery_missing_driver_raises_backend_error(monkeypatch):
    """Simulate google-cloud-bigquery not being installed."""
    import builtins

    from speaksql.backends import backend_for

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("google.cloud") or name == "google":
            raise ImportError("simulated: google-cloud-bigquery not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BackendError) as exc:
        backend_for("bigquery")
    assert "google-cloud-bigquery" in str(exc.value).lower()


def test_bigquery_accepts_prebuilt_client():
    """Passing a custom client should not trigger implicit Client() construction."""
    from speaksql.backends.bigquery_backend import BigQueryBackend

    class FakeClient:
        def close(self):
            pass

    be = BigQueryBackend(client=FakeClient())
    assert be._client is not None
    be.close()