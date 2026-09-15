"""Semantic diff between two SQL strings (possibly from different dialects).

The point is to show **what changed semantically**, not textually:
- function-call rewrites (same function, different arg order / quoting)
- null-ordering changes (NULLS FIRST / LAST)
- distinct-syntax changes (LIMIT vs TOP)
- type-name aliases (DOUBLE vs DOUBLE PRECISION)
- structural changes (different tables, columns, predicates)

Both inputs are parsed into canonical SQLGlot ASTs and compared
node-by-node. Each difference is classified and reported with enough
context to understand the change without re-reading both queries.

Output is `list[DiffEntry]` (or printable via `format_diff`):

    [
      DiffEntry(category='function_call', a='DATE_TRUNC(\\'MONTH\\', x)',
                b='DATE_TRUNC(x, MONTH)', location='SELECT #0'),
      DiffEntry(category='null_ordering', a=None, b='NULLS LAST', ...),
      ...
    ]

Pure function, no I/O, no SQL execution — safe to call on any pair of
strings SQLGlot can parse.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from speaksql.exceptions import TranspileError

# Function pairs SQLGlot considers equivalent despite textual differences.
# Populated empirically by transpile output; extend as more dialects land.
_FUNCTION_NORMALIZERS: dict[tuple[str, str], str] = {
    # (a_func, b_func) -> canonical_key. Each maps to the SAME canonical key.
    "date_trunc": "date_trunc",
    "dateadd": "date_add",
    "datediff": "date_diff",
    "ifnull": "coalesce",
    "nvl": "coalesce",
    "isnull": "coalesce",
    "concat": "concat",
    "length": "length",
    "len": "length",
}

# Type-name aliases — equivalent semantically.
_TYPE_ALIASES: dict[str, str] = {
    "double precision": "double",
    "double": "double",
    "float8": "double",
    "real": "double",
    "int": "int",
    "int4": "int",
    "integer": "int",
    "int8": "bigint",
    "bigint": "bigint",
    "varchar": "varchar",
    "character varying": "varchar",
    "text": "varchar",
    "bool": "boolean",
    "boolean": "boolean",
    "timestamp": "timestamp",
    "datetime": "timestamp",
    "date": "date",
}


class DiffEntry:
    """A single semantic difference between two SQL statements.

    Attributes:
        category: One of 'function_call', 'null_ordering', 'distinct_syntax',
                  'type_name', 'literal', 'predicate', 'structural', 'identical'.
        a: Text from the first SQL (or None if absent).
        b: Text from the second SQL (or None if absent).
        location: Human-readable position (e.g. 'SELECT #0', 'WHERE', 'ORDER BY #1').
        note: Optional explanatory comment.
    """

    __slots__ = ("a", "b", "category", "location", "note")

    def __init__(
        self,
        category: str,
        a: str | None,
        b: str | None,
        location: str,
        note: str | None = None,
    ) -> None:
        self.category = category
        self.a = a
        self.b = b
        self.location = location
        self.note = note

    def to_dict(self) -> dict[str, str | None]:
        return {
            "category": self.category,
            "location": self.location,
            "a": self.a,
            "b": self.b,
            "note": self.note,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"DiffEntry(category={self.category!r}, location={self.location!r}, "
            f"a={self.a!r}, b={self.b!r})"
        )


def semantic_diff(
    sql_a: str,
    sql_b: str,
    *,
    dialect_a: str = "",
    dialect_b: str = "",
) -> list[DiffEntry]:
    """Compute a list of semantic differences between two SQL strings.

    Parses both with SQLGlot, then walks the ASTs comparing:
    - SELECT projections (function calls, literals, column refs)
    - WHERE predicates (function rewrites, literal forms)
    - ORDER BY (null-ordering, asc/desc)
    - LIMIT / OFFSET / TOP (distinct syntax)
    - Type annotations in CAST / column types

    Returns an empty list if the two SQLs are semantically identical.
    """
    try:
        ast_a = sqlglot.parse_one(sql_a, dialect=dialect_a or None)
        ast_b = sqlglot.parse_one(sql_b, dialect=dialect_b or None)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as e:
        raise TranspileError(f"Failed to parse SQL for diff: {e}") from e

    diffs: list[DiffEntry] = []

    # 1) SELECT projections: walk both lists, pairwise compare
    _compare_projections(ast_a, ast_b, diffs)

    # 2) WHERE predicates: structural equality + function-call rewrite check
    _compare_where(ast_a, ast_b, diffs)

    # 3) ORDER BY: compare keys + null-ordering
    _compare_order_by(ast_a, ast_b, diffs)

    # 4) LIMIT / OFFSET / TOP: distinct-syntax checks
    _compare_limits(ast_a, ast_b, diffs)

    # 5) CAST and type annotations
    _compare_casts(ast_a, ast_b, diffs)

    # 6) Structural sanity: same FROM / JOINs?
    _compare_from(ast_a, ast_b, diffs)

    return diffs


def format_diff(diffs: list[DiffEntry]) -> str:
    """Render a DiffEntry list as a human-readable summary."""
    if not diffs:
        return "No semantic differences.\n"
    lines = [f"{len(diffs)} semantic difference(s):"]
    for i, d in enumerate(diffs, 1):
        a_str = repr(d.a) if d.a is not None else "<absent>"
        b_str = repr(d.b) if d.b is not None else "<absent>"
        note = f"  ({d.note})" if d.note else ""
        lines.append(f"  {i}. [{d.category}] @ {d.location}")
        lines.append(f"     a: {a_str}")
        lines.append(f"     b: {b_str}{note}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compare_projections(
    ast_a: exp.Expression,
    ast_b: exp.Expression,
    diffs: list[DiffEntry],
) -> None:
    sel_a = ast_a.find(exp.Select)
    sel_b = ast_b.find(exp.Select)
    if sel_a is None or sel_b is None:
        return
    exprs_a = list(sel_a.expressions)
    exprs_b = list(sel_b.expressions)
    # Pad shorter list with sentinels so pairwise comparison still runs
    n = max(len(exprs_a), len(exprs_b))
    for i in range(n):
        ea = exprs_a[i] if i < len(exprs_a) else None
        eb = exprs_b[i] if i < len(exprs_b) else None
        loc = f"SELECT #{i}"
        if ea is None:
            diffs.append(
                DiffEntry("structural", None, eb.sql(), loc, "extra projection in b")
            )
            continue
        if eb is None:
            diffs.append(
                DiffEntry("structural", ea.sql(), None, loc, "extra projection in a")
            )
            continue
        # Function-call rewrite check
        _diff_function_calls(ea, eb, diffs, loc)
        # Literal comparison (quotes / forms)
        _diff_literals(ea, eb, diffs, loc)


def _compare_where(
    ast_a: exp.Expression,
    ast_b: exp.Expression,
    diffs: list[DiffEntry],
) -> None:
    where_a = ast_a.find(exp.Where)
    where_b = ast_b.find(exp.Where)
    if where_a is None and where_b is None:
        return
    if where_a is None:
        diffs.append(
            DiffEntry("predicate", None, where_b.sql(), "WHERE", "added in b")
        )
        return
    if where_b is None:
        diffs.append(
            DiffEntry("predicate", where_a.sql(), None, "WHERE", "removed in b")
        )
        return
    if (
        where_a.this.sql() != where_b.this.sql()
        and not _diff_function_calls(where_a.this, where_b.this, diffs, "WHERE")
    ):
        diffs.append(
            DiffEntry(
                "predicate", where_a.sql(), where_b.sql(), "WHERE", "predicate text differs"
            )
        )


def _compare_order_by(
    ast_a: exp.Expression,
    ast_b: exp.Expression,
    diffs: list[DiffEntry],
) -> None:
    order_a = ast_a.find(exp.Order)
    order_b = ast_b.find(exp.Order)
    if order_a is None and order_b is None:
        return
    if order_a is None or order_b is None:
        diffs.append(
            DiffEntry(
                "structural",
                order_a.sql() if order_a else None,
                order_b.sql() if order_b else None,
                "ORDER BY",
                "ORDER BY clause difference",
            )
        )
        return
    exprs_a = list(order_a.expressions)
    exprs_b = list(order_b.expressions)
    n = max(len(exprs_a), len(exprs_b))
    for i in range(n):
        ea = exprs_a[i] if i < len(exprs_a) else None
        eb = exprs_b[i] if i < len(exprs_b) else None
        loc = f"ORDER BY #{i}"
        if ea is None or eb is None:
            diffs.append(
                DiffEntry(
                    "structural",
                    ea.sql() if ea else None,
                    eb.sql() if eb else None,
                    loc,
                    "extra ORDER BY key",
                )
            )
            continue
        # Null-ordering check
        nulls_a = _nulls_sql(ea)
        nulls_b = _nulls_sql(eb)
        if nulls_a != nulls_b:
            diffs.append(
                DiffEntry(
                    "null_ordering",
                    nulls_a,
                    nulls_b,
                    loc,
                    "null ordering differs",
                )
            )
        # Direction check (ASC vs DESC)
        dir_a = "DESC" if ea.args.get("desc") else "ASC"
        dir_b = "DESC" if eb.args.get("desc") else "ASC"
        if dir_a != dir_b:
            diffs.append(
                DiffEntry(
                    "distinct_syntax",
                    dir_a,
                    dir_b,
                    loc,
                    "sort direction differs",
                )
            )


def _compare_limits(
    ast_a: exp.Expression,
    ast_b: exp.Expression,
    diffs: list[DiffEntry],
) -> None:
    limit_a = ast_a.args.get("limit")
    limit_b = ast_b.args.get("limit")
    top_a = _find_top(ast_a)
    top_b = _find_top(ast_b)
    # Snowflake/SQL Server use TOP; everyone else uses LIMIT
    if top_a and not limit_b:
        diffs.append(
            DiffEntry(
                "distinct_syntax",
                top_a.sql(),
                None,
                "row limit",
                "TOP used in a; b has no equivalent LIMIT",
            )
        )
        return
    if top_b and not limit_a:
        diffs.append(
            DiffEntry(
                "distinct_syntax",
                None,
                top_b.sql(),
                "row limit",
                "b uses TOP; a has no equivalent LIMIT",
            )
        )
        return
    if limit_a and limit_b and limit_a.sql() != limit_b.sql():
        diffs.append(
            DiffEntry(
                "distinct_syntax",
                limit_a.sql(),
                limit_b.sql(),
                "LIMIT",
                "limit clause differs",
            )
        )


def _compare_casts(
    ast_a: exp.Expression,
    ast_b: exp.Expression,
    diffs: list[DiffEntry],
) -> None:
    """Compare CAST expressions: same value, different type name?"""
    for ca in ast_a.find_all(exp.Cast):
        cb = _find_equivalent_cast(ca, ast_b)
        if cb is None:
            continue
        ta = _normalize_type(ca.to.sql())
        tb = _normalize_type(cb.to.sql())
        if ta != tb and ta is not None and tb is not None:
            diffs.append(
                DiffEntry(
                    "type_name",
                    ca.sql(),
                    cb.sql(),
                    "CAST",
                    f"type alias: {ta!r} vs {tb!r}",
                )
            )


def _compare_from(
    ast_a: exp.Expression,
    ast_b: exp.Expression,
    diffs: list[DiffEntry],
) -> None:
    from_a = ast_a.find(exp.From)
    from_b = ast_b.find(exp.From)
    if from_a is None or from_b is None:
        return
    # Just compare the text for now — full graph diffing is out of scope.
    if from_a.this.sql() != from_b.this.sql():
        diffs.append(
            DiffEntry(
                "structural",
                from_a.sql(),
                from_b.sql(),
                "FROM",
                "source tables differ",
            )
        )


def _diff_function_calls(
    ea: exp.Expression,
    eb: exp.Expression,
    diffs: list[DiffEntry],
    location: str,
) -> bool:
    """If both sides are function calls with the same canonical key but
    different textual forms, record the diff. Return True iff a diff was
    added (used by _compare_where to decide between predicate-text diff
    vs function-rewrite diff)."""
    # In SQLGlot, both built-in and vendor-specific functions parse as
    # Anonymous expressions. There's no separate Function base class.
    fa = ea.find(exp.Anonymous)
    fb = eb.find(exp.Anonymous)
    if fa is None or fb is None:
        return False
    key_a = _FUNCTION_NORMALIZERS.get(fa.sql_name().lower(), fa.sql_name().lower())
    key_b = _FUNCTION_NORMALIZERS.get(fb.sql_name().lower(), fb.sql_name().lower())
    if key_a == key_b and fa.sql() != fb.sql():
        diffs.append(
            DiffEntry(
                "function_call",
                fa.sql(),
                fb.sql(),
                location,
                f"same canonical function '{key_a}', different call form",
            )
        )
        return True
    return False


def _diff_literals(
    ea: exp.Expression,
    eb: exp.Expression,
    diffs: list[DiffEntry],
    location: str,
) -> bool:
    la = ea.find(exp.Literal)
    lb = eb.find(exp.Literal)
    if la is None or lb is None:
        return False
    if la.this == lb.this and la.sql() != lb.sql():
        diffs.append(
            DiffEntry(
                "literal",
                la.sql(),
                lb.sql(),
                location,
                "same value, different quoting",
            )
        )
        return True
    return False


def _nulls_sql(node: exp.Ordered) -> str | None:
    """Extract NULLS FIRST/LAST from an Ordered expression, if present.

    SQLGlot stores the specifier as `nulls_first` (bool) on the Ordered node.
    None means no explicit specifier was set.
    """
    n = node.args.get("nulls_first")
    if n is True:
        return "NULLS FIRST"
    if n is False:
        return "NULLS LAST"
    return None


def _find_top(ast: exp.Expression) -> exp.Expression | None:
    """SQL Server / Snowflake use SELECT TOP N; we look for it on the Select node."""
    top = ast.args.get("top")
    return top


def _find_equivalent_cast(
    target_cast: exp.Cast,
    other_ast: exp.Expression,
) -> exp.Cast | None:
    """Find a CAST in `other_ast` whose inner expression matches `target_cast.this`."""
    target_inner = target_cast.this
    for c in other_ast.find_all(exp.Cast):
        if c.this.sql() == target_inner.sql():
            return c
    return None


def _normalize_type(t: str) -> str | None:
    """Map a type name to its canonical form. Returns None if unknown."""
    return _TYPE_ALIASES.get(t.lower().strip())


__all__ = ["DiffEntry", "format_diff", "semantic_diff"]