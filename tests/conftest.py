"""Pytest fixtures + helpers for SpeakSQL."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# Make src/ importable without an install
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


_SERVICE_DEPS = ("fastapi", "starlette", "httpx")


def _service_extras_available() -> bool:
    return all(importlib.util.find_spec(name) for name in _SERVICE_DEPS)


# Skip the entire `service` module if optional deps are absent so the
# default test run is robust on either install profile.
if not _service_extras_available():
    pytest.skip(
        "fastapi/starlette/httpx not installed; skipping service tests",
        allow_module_level=True,
    )


@pytest.fixture(scope="session")
def service_extras_available() -> bool:
    return _service_extras_available()