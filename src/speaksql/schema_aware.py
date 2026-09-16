"""FK-aware SQL generation.

Given a schema (with real FK metadata from `Backend.foreign_keys()`),
this module:

1. Identifies which tables the question refers to (token matching
   against table names + column names).
2. Picks the right JOIN columns using the FK metadata.
3. Returns a `SchemaHint` that downstream consumers (the rule-based
   planner, the LLM system prompt, the CLI) can use to generate
   queries that actually use the right JOIN shape.

The module is deliberately small and synchronous — it's a heuristic,
not a planner. The LLM still does the heavy lifting for ambiguous
queries; this module's job is to make the easy cases unambiguous and
to feed the LLM a tight summary of the relevant schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from speaksql.introspect import ForeignKeyInfo, SchemaList


@dataclass
class SchemaHint:
    """A planner-friendly summary of which tables/columns the question
    refers to, plus the JOIN edges between them.

    Attributes:
        tables: Tables the question references, ordered by relevance.
        columns: Columns the question references, by table name.
        joins: FK-derived JOIN edges between the chosen tables.
        unresolved: Tokens that look table/column-like but didn't match
            anything in the schema. Useful for asking the user to
            disambiguate, or for the LLM prompt.
    """

    tables: tuple[str, ...]
    columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    joins: tuple[JoinEdge, ...] = field(default_factory=tuple)
    unresolved: tuple[str, ...] = field(default_factory=tuple)

    def to_prompt_section(self) -> str:
        """Render a compact summary suitable for an LLM system prompt.

        Example output:

            Tables: orders (user_id, total, created_at), users (email, country)
            JOINs:
              orders.user_id → users.id
        """
        if not self.tables:
            return "Tables: (none matched)"
        lines = ["Tables:"]
        for t in self.tables:
            cols = self.columns.get(t, ())
            col_list = ", ".join(cols) if cols else "(no columns matched)"
            lines.append(f"  {t} ({col_list})")
        if self.joins:
            lines.append("Joins:")
            for j in self.joins:
                lines.append(f"  {j.from_table}.{j.from_column} → {j.to_table}.{j.to_column}")
        if self.unresolved:
            lines.append(f"Unresolved tokens: {', '.join(self.unresolved)}")
        return "\n".join(lines)


@dataclass
class JoinEdge:
    """A specific JOIN recommendation derived from real FK metadata."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    constraint_name: str | None = None


def plan_for_question(question: str, schema: SchemaList) -> SchemaHint:
    """Build a SchemaHint by matching the question against the schema.

    Tokenization is intentionally simple — split on whitespace, strip
    punctuation, lowercase. We score each token against:

    - exact table name match (high weight)
    - pluralized table name (e.g. 'users' matches table 'user')
    - column name match (across all tables)
    - `{singular(table_name)}_id` matches a column name

    When a question mentions an FK column (e.g. `user_id` in a query about
    "orders where user_id is set"), the FK metadata in `schema.foreign_keys`
    is consulted to also pull in the parent table — so the planner can
    recommend a JOIN.

    Once tables are chosen, we look up JOIN edges from
    `schema.foreign_keys` and pick the ones that connect two chosen
    tables. Multi-hop paths are not handled — that's the LLM's job.
    """
    tokens = _tokenize(question)
    if not tokens:
        return SchemaHint(tables=())

    table_scores: dict[str, int] = {}
    column_hits: dict[str, set[str]] = {}

    # Index FKs by from_table so we can resolve FK columns → parent tables.
    fk_by_from_table: dict[str, list[ForeignKeyInfo]] = {}
    fk_by_to_table: dict[str, list[ForeignKeyInfo]] = {}
    for fk in schema.foreign_keys:
        fk_by_from_table.setdefault(_table_only(fk.from_table), []).append(fk)
        fk_by_to_table.setdefault(_table_only(fk.to_table), []).append(fk)

    for token in tokens:
        # Score table name matches
        for t in schema.tables:
            t_lower = t.name.lower()
            if token == t_lower:
                table_scores[t.name] = table_scores.get(t.name, 0) + 5
            elif token == _singular(t_lower):
                table_scores[t.name] = table_scores.get(t.name, 0) + 4
            elif token in t_lower or t_lower in token:
                # Substring match — give partial credit (e.g. 'order' vs 'order_items')
                table_scores[t.name] = table_scores.get(t.name, 0) + 1

        # Score column name matches (table → set of matched column names)
        for t in schema.tables:
            for c in t.columns:
                c_lower = c.name.lower()
                if token == c_lower:
                    table_scores[t.name] = table_scores.get(t.name, 0) + 2
                    column_hits.setdefault(t.name, set()).add(c.name)
                elif token == f"{_singular(t.name)}_id" and c_lower == f"{_singular(t.name)}_id":
                    # `{singular(other)}_id` match — likely an FK column
                    table_scores[t.name] = table_scores.get(t.name, 0) + 3
                    column_hits.setdefault(t.name, set()).add(c.name)

    # If the question mentions an FK column explicitly, also pull in the
    # FK's parent table (because the user almost certainly wants to join).
    # Example: "orders where user_id is set" → user_id is on orders; the FK
    # orders.user_id → users.id means the question is also about users.
    for t_name, cols in column_hits.items():
        for col in cols:
            for fk in fk_by_from_table.get(t_name, ()):
                if fk.from_column == col:
                    parent = _table_only(fk.to_table)
                    if parent not in table_scores:
                        table_scores[parent] = 1
                    break

    # Bonus round: if a non-selected table is a substring match of an
    # already-selected table AND has an FK to a selected table, boost
    # it. Example: "order_items" selected → "orders" substring bonus.
    # This catches cases where the user said "order_items" but the
    # statement they want to write needs both tables.
    for sel in list(table_scores):
        for t in schema.tables:
            if t.name == sel:
                continue
            t_lower = t.name.lower()
            sel_lower = sel.lower()
            if t_lower in sel_lower or sel_lower in t_lower:
                # Has substring relation — check for FK connection
                for fk in schema.foreign_keys:
                    if (
                        _table_only(fk.from_table) == _table_only(t.name)
                        and _table_only(fk.to_table) == _table_only(sel)
                    ) or (
                        _table_only(fk.to_table) == _table_only(t.name)
                        and _table_only(fk.from_table) == _table_only(sel)
                    ):
                        table_scores[t.name] = table_scores.get(t.name, 0) + 2
                        break

    # Pick the top 2-3 tables by score (but always at least one if anything matched).
    if not table_scores:
        return SchemaHint(tables=(), unresolved=tuple(tokens))

    ranked = sorted(table_scores.items(), key=lambda kv: (-kv[1], kv[0]))
    chosen = list(ranked[:3])
    chosen_names = [name for name, _ in chosen]

    # Multi-hop: if the chosen tables are not directly FK-connected, do a
    # BFS through the FK graph to find shortest paths between them. This
    # pulls in intermediate tables that the user didn't name explicitly
    # but are required to make the JOINs work.
    # Example: question mentions "users" and "order_items" — no direct FK
    # between them, but via "orders" they connect. The BFS finds that path
    # and adds "orders" to the chosen set.
    def _bfs_shortest_path(
        src: str, dst: str, edges: list[tuple[str, str, str]]
    ) -> list[str] | None:
        """Unweighted BFS over the FK graph. Returns the list of
        intermediate table names (excluding src and dst themselves),
        or None if no path exists."""
        if src == dst:
            return []
        # Adjacency: table_name -> [(neighbor_name, edge_label)]
        adj: dict[str, list[tuple[str, str]]] = {}
        for a, b, label in edges:
            adj.setdefault(a, []).append((b, label))
            adj.setdefault(b, []).append((a, label))
        if src not in adj or dst not in adj:
            return None
        # BFS
        from collections import deque

        visited: dict[str, str | None] = {src: None}
        queue = deque([src])
        while queue:
            node = queue.popleft()
            if node == dst:
                # Reconstruct path by following parent pointers.
                # We start with the dst node itself, then walk backward.
                path: list[str] = [node]
                cur = node
                while visited[cur] is not None:
                    cur = visited[cur]  # type: ignore[assignment]
                    path.append(cur)
                path.reverse()
                # path is now [src, ..., dst]; strip endpoints to get
                # intermediate tables only.
                return path[1:-1]
            for neighbor, _ in adj.get(node, ()):
                if neighbor not in visited:
                    visited[neighbor] = node
                    queue.append(neighbor)
        return None

    # Build the FK edge list (as tuple of (from, to, column-pair-label)).
    fk_edges: list[tuple[str, str, str]] = []
    for fk in schema.foreign_keys:
        a = _table_only(fk.from_table)
        b = _table_only(fk.to_table)
        label = f"{a}.{fk.from_column} -> {b}.{fk.to_column}"
        fk_edges.append((a, b, label))

    # For every pair of chosen tables, find the shortest FK path
    # and add any intermediate tables to the chosen set (with a small
    # score so they rank below the user's explicit picks).
    added_intermediates: set[str] = set()
    for i in range(len(chosen_names)):
        for j in range(i + 1, len(chosen_names)):
            path = _bfs_shortest_path(chosen_names[i], chosen_names[j], fk_edges)
            if path is None:
                continue
            for t in path:
                if t not in added_intermediates and t not in chosen_names:
                    added_intermediates.add(t)
                    # Add at the END so user-explicit picks still rank first
                    chosen_names.append(t)
    # Rebuild chosen as a tuple
    chosen = tuple(chosen_names)

    # Find FK edges connecting two chosen tables.
    # FK tables can be fully qualified ("schema.table") — normalize to just the table name for the membership check.
    chosen_set = {_table_only(name) for name in chosen}
    joins: list[JoinEdge] = []
    for fk in schema.foreign_keys:
        if _table_only(fk.from_table) in chosen_set and _table_only(fk.to_table) in chosen_set:
            joins.append(
                JoinEdge(
                    from_table=_table_only(fk.from_table),
                    from_column=fk.from_column,
                    to_table=_table_only(fk.to_table),
                    to_column=fk.to_column,
                    constraint_name=fk.constraint_name,
                )
            )

    # Unresolved: tokens that scored zero
    matched_tokens = set()
    for t in chosen:
        matched_tokens.add(t.lower())
        for c in column_hits.get(t, ()):
            matched_tokens.add(c.lower())
    # Also count table-name substrings as matched
    for t in schema.tables:
        if t.name in chosen:
            for tok in tokens:
                if tok in t.name.lower():
                    matched_tokens.add(tok)
    unresolved = tuple(tok for tok in tokens if tok not in matched_tokens)

    return SchemaHint(
        tables=chosen,
        columns={t: tuple(sorted(column_hits.get(t, ()))) for t in chosen},
        joins=tuple(joins),
        unresolved=unresolved,
    )


def _table_only(name: str) -> str:
    """Return the unqualified table name (drop schema/catalog prefix)."""
    return name.rsplit(".", 1)[-1]


def _tokenize(question: str) -> list[str]:
    """Lowercase + strip punctuation, then split on whitespace."""
    cleaned = re.sub(r"[^\w\s]", " ", question.lower())
    return [tok for tok in cleaned.split() if tok]


def _singular(name: str) -> str:
    """Rough singular form: 'users' → 'user', 'categories' → 'category'."""
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("es") and not name.endswith("ses"):
        return name[:-2]
    if name.endswith("s"):
        return name[:-1]
    return name


def joins_to_sql(joins: tuple[JoinEdge, ...]) -> str:
    """Render JOIN edges as a SQL JOIN clause.

    Example output:

        JOIN users ON orders.user_id = users.id
        JOIN order_items ON orders.id = order_items.order_id
    """
    parts = []
    for j in joins:
        parts.append(
            f"JOIN {j.to_table} ON {j.from_table}.{j.from_column} = {j.to_table}.{j.to_column}"
        )
    return "\n".join(parts)


__all__ = ["JoinEdge", "SchemaHint", "joins_to_sql", "plan_for_question"]