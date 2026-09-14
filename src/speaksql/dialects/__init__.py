"""Dialect-specific override modules.

Each module exposes the same surface: any overrides / pre-rewrites the
canonical ANSI plan needs before it reaches SQLGlot's emitter.
"""

from __future__ import annotations

from typing import Any

# These are re-exported by the package; concrete overrides are applied in
# core._apply_overrides. New dialects add a module here and a key in the
# override map.

__all__: list[str] = []


def register(name: str) -> Any:
    """Decorator hook used by future plugin-style dialect registration."""
    def deco(obj: Any) -> Any:
        obj._speaksql_dialect = name  # type: ignore[attr-defined]
        return obj
    return deco