"""SAP HANA dialect for SQLGlot.

HANA is broadly ANSI-compatible, but ships its own function library
(`ADD_MONTHS`, `SERIES_GENERATE_DATE`, `MAP`, `BINNING`, `DAYS_BETWEEN`,
`SECONDS_BETWEEN`, `TO_VARCHAR`, etc.).

We vendor a HANA dialect locally because upstream SQLGlot does not yet
ship one. The class subclasses Postgres because HANA inherits most of
its parser grammar; the only override we need today is removing the
2-arity `MAP` binding Postgres enforces (HANA's MAP takes variable
even-arity pairs).

This module is **only** loaded when a HANA transpile or parse is
requested — it costs nothing on the hot path for other dialects.
"""

from __future__ import annotations

from typing import ClassVar

from sqlglot.dialects.dialect import Dialect as _BaseDialect
from sqlglot.dialects.postgres import Postgres


class Hana(Postgres):
    """SAP HANA dialect — subclass of Postgres for ANSI compatibility."""

    _HANA = True
    NORMALIZE_FUNCTIONS = False  # HANA keeps function names as-is

    class Tokenizer(Postgres.Tokenizer):
        """HANA uses the same lexical conventions as Postgres."""

    class Parser(Postgres.Parser):
        """HANA parses almost identically to Postgres.

        The only override we need today is removing Postgres's 2-arity
        `MAP` binding (HANA's MAP takes variable even-arity pairs).
        Unbound functions fall through to the anonymous path and render
        as `MAP(...)` verbatim.
        """

        # SQLGlot parses `FUNCTIONS` once at class-creation time; we copy
        # the parent table and remove MAP so it falls through to anonymous.
        FUNCTIONS: ClassVar[dict] = {
            k: v for k, v in Postgres.Parser.FUNCTIONS.items() if k != "MAP"
        }

    class Generator(Postgres.Generator):
        """Generator — emits SQL in HANA-flavoured form.

        HANA is functionally identical to Postgres for the SQL features
        we support; we inherit Postgres's generator unchanged.
        """


# Register with SQLGlot so `dialect="hana"` works in `parse_one` / `transpile`.
# We add to Dialect.classes (a dict), which is SQLGlot's supported extension
# point for vendor dialects.
Dialect = Hana  # re-export for `from speaksql.dialects.hana import Dialect`


def _register() -> None:
    """Register the HANA dialect with SQLGlot if not already present."""
    _BaseDialect.classes.setdefault("hana", Hana)


_register()


__all__ = ["Dialect", "Hana", "_register"]