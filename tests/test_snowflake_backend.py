"""Smoke tests for the Snowflake backend constructor.

Snowflake needs an account, user, password — none available in CI. So we
only verify the driver loads, the class implements the Backend protocol,
and missing-driver failures surface cleanly.
"""

from __future__ import annotations

import pytest
from speaksql.exceptions import BackendError


def test_snowflake_constructor_class_exists():
    from speaksql.backends.snowflake_backend import SnowflakeBackend

    assert hasattr(SnowflakeBackend, "introspect")
    assert hasattr(SnowflakeBackend, "execute")
    assert hasattr(SnowflakeBackend, "close")


def test_snowflake_missing_driver_raises_backend_error(monkeypatch):
    """Simulate snowflake-connector-python not being installed."""
    import builtins

    from speaksql.backends import backend_for

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "snowflake" or name.startswith("snowflake."):
            raise ImportError("simulated: snowflake-connector-python not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BackendError) as exc:
        backend_for("snowflake")
    assert "snowflake-connector-python" in str(exc.value).lower()