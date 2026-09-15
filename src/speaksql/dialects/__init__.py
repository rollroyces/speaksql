"""Dialect-specific override modules.

Each module exposes dialect-specific SQLGlot extensions. Currently only
HANA ships a real implementation (vendored locally because upstream
SQLGlot lacks one); other dialects rely on SQLGlot's built-in support.

To add a vendor dialect: create a new module that subclasses the
closest base and registers it via `Dialect.classes.setdefault(...)`.
"""

from __future__ import annotations

from typing import Any

# Concrete dialects live in submodules and self-register on import.
from . import hana as _hana  # noqa: F401  — side effect: registers 'hana'

__all__: list[str] = ["hana"]


def register(name: str) -> Any:
    """Decorator hook used by future plugin-style dialect registration."""

    def deco(obj: Any) -> Any:
        obj._speaksql_dialect = name  # type: ignore[attr-defined]
        return obj

    return deco