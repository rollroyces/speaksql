"""Pytest fixtures + helpers for SpeakSQL."""

from __future__ import annotations

import os
import shutil
import sys

import pytest

# Make src/ importable without an install
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def pytest_collection_modifyitems(config, items):
    """Skip service tests if fastapi isn't installed."""
    if shutil.which("python") is None:
        return
    try:
        import fastapi  # noqa: F401
    except ImportError:
        skip_service = pytest.mark.skip(reason="fastapi not installed")
        for item in items:
            if "service" in item.keywords:
                item.add_marker(skip_service)