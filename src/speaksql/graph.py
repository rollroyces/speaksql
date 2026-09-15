"""JOIN graph builder and HTML renderer.

Given a `SchemaList`, build a graph where:
- Each table is a node
- Each pair of tables that share a column name (heuristic) is an edge

This is a **heuristic** — real JOIN relationships are inferred from FK
constraints, but SpeakSQL doesn't read those yet. Shared column names
are the next-best signal, and they work well in practice for many
schemas (id, user_id, order_id, etc.).

Output: a `JoinGraph` dataclass plus `render_html()` that produces a
standalone HTML file (zero external deps, inlined SVG).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from speaksql.introspect import ColumnInfo, SchemaList, TableInfo


@dataclass
class TableNode:
    """A single table in the join graph."""

    name: str
    schema: str | None
    catalog: str | None
    columns: tuple[ColumnInfo, ...]
    # Column names that are shared with at least one other table.
    join_columns: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class JoinEdge:
    """An inferred JOIN between two tables."""

    source: str  # "schema.table" or "table"
    target: str  # "schema.table" or "table"
    columns: tuple[str, ...]  # column names shared by both


@dataclass
class JoinGraph:
    """Heuristic JOIN graph built from a schema."""

    nodes: tuple[TableNode, ...]
    edges: tuple[JoinEdge, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": [
                {
                    "name": n.name,
                    "schema": n.schema,
                    "catalog": n.catalog,
                    "columns": [
                        {
                            "name": c.name,
                            "data_type": c.data_type,
                            "is_primary_key": c.is_primary_key,
                            "nullable": c.nullable,
                        }
                        for c in n.columns
                    ],
                    "join_columns": list(n.join_columns),
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "columns": list(e.columns),
                }
                for e in self.edges
            ],
        }


def _table_key(t: TableInfo) -> str:
    """Stable identifier for a table: 'schema.name' or 'name'."""
    return f"{t.schema}.{t.name}" if t.schema else t.name


def build_join_graph(schema: SchemaList) -> JoinGraph:
    """Build a JOIN graph using name-based FK heuristics.

    Heuristics, in order of strength:

    1. **FK by name (strongest):** If `orders.user_id` and `users.id`
       both exist with the same data type, that's a clear FK edge.
       Concretely: a column named `{singular(other)}_id` matches the PK
       or named column on the other side.
    2. **Same-name shared column (weaker):** If both tables have a column
       named `foo` of the same type, that's a candidate — but we skip
       the case where it's the PK on *both* sides (PK↔PK is identity,
       not a join).
    3. **Type mismatch disqualifies** any candidate — `description TEXT`
       vs `description INTEGER` shouldn't be linked.

    This is still a heuristic — real FK constraints from information_schema
    are the gold standard and would be preferred when available.
    """
    tables = schema.tables

    def _singular(name: str) -> str:
        """Rough singular form: 'users' -> 'user', 'order_items' -> 'order_item'."""
        if name.endswith("ies"):
            return name[:-3] + "y"
        if name.endswith("es") and not name.endswith("ses"):
            return name[:-2]
        if name.endswith("s"):
            return name[:-1]
        return name

    # Index: col_name -> {table_key: (data_type, is_pk)}
    col_index: dict[str, dict[str, tuple[str, bool]]] = {}
    pk_columns: dict[str, set[str]] = {}  # table_key -> {pk_column_names}
    for t in tables:
        key = _table_key(t)
        pk_columns[key] = set()
        for c in t.columns:
            col_index.setdefault(c.name, {})[key] = (c.data_type, c.is_primary_key)
            if c.is_primary_key:
                pk_columns[key].add(c.name)

    edges: list[JoinEdge] = []
    seen_pairs: dict[tuple[str, str], list[str]] = {}

    def _consider(t1: TableInfo, t2: TableInfo, c1: ColumnInfo, c2: ColumnInfo) -> None:
        pair = tuple(sorted((_table_key(t1), _table_key(t2))))
        if _table_key(t1) == _table_key(t2):
            return  # same table
        if c1.data_type.lower().strip() != c2.data_type.lower().strip():
            return
        # Skip PK ↔ PK of the same name (identity, not a join)
        s1, s2 = _singular(t1.name), _singular(t2.name)
        is_fk_to_t1 = c1.name == f"{s1}_id" or c2.name == f"{s1}_id"
        is_fk_to_t2 = c1.name == f"{s2}_id" or c2.name == f"{s2}_id"
        both_pk_same_name = (
            c1.is_primary_key
            and c2.is_primary_key
            and c1.name == c2.name
        )
        if both_pk_same_name and not (is_fk_to_t1 or is_fk_to_t2):
            return  # PK ↔ PK with no FK signal: skip
        seen_pairs.setdefault(pair, []).append(c1.name)

    for t1 in tables:
        for t2 in tables:
            if _table_key(t1) >= _table_key(t2):
                continue
            # FK heuristic: does t1 have a column named like t2?
            s2 = _singular(t2.name)
            for c1 in t1.columns:
                # c1 might be the FK side pointing at t2's PK
                if c1.name == f"{s2}_id":
                    # find t2's PK of the same type
                    for pk_name in pk_columns[_table_key(t2)]:
                        c2_info = col_index.get(pk_name, {}).get(_table_key(t2))
                        if c2_info and c2_info[0].lower() == c1.data_type.lower():
                            seen_pairs.setdefault(
                                (_table_key(t1), _table_key(t2)), []
                            ).append(c1.name)
            # Symmetric: same-name columns of the same type (excluding PK↔PK)
            for c1 in t1.columns:
                other_owners = col_index.get(c1.name, {})
                c2_info = other_owners.get(_table_key(t2))
                if not c2_info:
                    continue
                c2_type, c2_is_pk = c2_info
                if c2_type.lower() != c1.data_type.lower():
                    continue
                # Build a fake ColumnInfo for c2 to pass to _consider
                from speaksql.introspect import ColumnInfo

                c2 = ColumnInfo(
                    name=c1.name,
                    data_type=c2_type,
                    nullable=True,
                    is_primary_key=c2_is_pk,
                )
                _consider(t1, t2, c1, c2)

    for (src, tgt), cols in seen_pairs.items():
        edges.append(JoinEdge(src, tgt, tuple(cols)))

    # Mark join columns on each node
    join_cols_per_node: dict[str, set[str]] = {}
    for e in edges:
        join_cols_per_node.setdefault(e.source, set()).update(e.columns)
        join_cols_per_node.setdefault(e.target, set()).update(e.columns)

    nodes = tuple(
        TableNode(
            name=t.name,
            schema=t.schema,
            catalog=t.catalog,
            columns=t.columns,
            join_columns=tuple(sorted(join_cols_per_node.get(_table_key(t), set()))),
        )
        for t in tables
    )

    return JoinGraph(nodes=nodes, edges=tuple(edges))


def render_html(graph: JoinGraph, *, title: str = "Schema JOIN Graph") -> str:
    """Render the JOIN graph as a self-contained HTML file (inline SVG).

    No JS, no external CSS, no CDN dependencies — works fully offline.
    Tables are rendered as boxes; edges as straight lines through the
    shared columns. Layout is a simple radial arrangement around the
    center of mass; for very large schemas you'll want a real layout
    engine, but this scales comfortably up to ~20 tables.
    """
    if not graph.nodes:
        return (
            f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{title}</title></head><body><h1>{title}</h1>"
            f"<p>No tables.</p></body></html>"
        )

    # Layout: place nodes around a circle. Radius scales with count.
    n = len(graph.nodes)
    cx, cy = 500, 360
    radius = 280 if n <= 6 else min(380, 80 + n * 18)
    import math

    positions: dict[str, tuple[float, float]] = {}
    for i, node in enumerate(graph.nodes):
        angle = 2 * math.pi * i / n - math.pi / 2
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        positions[_node_key(node)] = (x, y)

    # Build the SVG
    node_w, node_h = 160, 60
    svg_parts: list[str] = [
            (
                "<svg width='1000' height='720' viewBox='0 0 1000 720' "
                "xmlns='http://www.w3.org/2000/svg'>"
            ),
        ]

    # Edges first (so they sit under nodes visually)
    for e in graph.edges:
        sp = positions.get(e.source)
        tp = positions.get(e.target)
        if not sp or not tp:
            continue
        cols_str = ", ".join(e.columns)
        svg_parts.append(
            f"<line x1='{sp[0]}' y1='{sp[1]}' x2='{tp[0]}' y2='{tp[1]}' "
            f"stroke='#94a3b8' stroke-width='1.5' "
            f"data-shared='{_escape(cols_str)}'/>"
        )
        # Label at the midpoint
        mid_x = (sp[0] + tp[0]) / 2
        mid_y = (sp[1] + tp[1]) / 2
        svg_parts.append(
            f"<text x='{mid_x}' y='{mid_y}' font-size='10' "
            f"fill='#475569' text-anchor='middle'>{_escape(cols_str)}</text>"
        )

    # Nodes
    for node in graph.nodes:
        x, y = positions[_node_key(node)]
        x0 = x - node_w / 2
        y0 = y - node_h / 2
        svg_parts.append(
            f"<g><rect x='{x0}' y='{y0}' width='{node_w}' height='{node_h}' "
            f"rx='6' fill='#f1f5f9' stroke='#0f172a' stroke-width='1.2'/>"
            f"<text x='{x}' y='{y0 + 18}' font-size='13' font-weight='600' "
            f"text-anchor='middle' fill='#0f172a'>{_escape(node.name)}</text>"
            f"<text x='{x}' y='{y0 + 36}' font-size='10' "
            f"text-anchor='middle' fill='#475569'>{len(node.columns)} cols · "
            f"{len(node.join_columns)} join</text></g>"
        )

    svg_parts.append("</svg>")

    svg = "\n  ".join(svg_parts)

    # Build the side table with column listings
    table_rows = []
    for node in graph.nodes:
        cols_html = "<br>".join(
            f"{_escape(c.name)}<span style='color:#64748b'> · "
            f"{_escape(c.data_type)}</span>"
            + (" <span style='color:#16a34a'>PK</span>" if c.is_primary_key else "")
            + (" <span style='color:#dc2626'>*</span>" if c.name in node.join_columns else "")
            for c in node.columns
        )
        table_rows.append(
            f"<tr><td style='vertical-align:top'><strong>{_escape(node.name)}</strong>"
            f"</td><td>{cols_html}</td></tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{_escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
            sans-serif; margin: 24px; color: #0f172a; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    p.lead {{ margin: 0 0 16px; color: #64748b; }}
    .layout {{ display: grid; grid-template-columns: 2fr 1fr; gap: 24px;
               align-items: start; }}
    @media (max-width: 900px) {{ .layout {{ grid-template-columns: 1fr; }} }}
    .legend {{ font-size: 12px; color: #64748b; margin-bottom: 8px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    td {{ padding: 6px 8px; border-bottom: 1px solid #e2e8f0;
          font-size: 13px; }}
  </style>
</head>
<body>
  <h1>{_escape(title)}</h1>
  <p class="lead">
    {n} tables · {len(graph.edges)} inferred joins ·
    <span style="color:#dc2626">*</span> joinable column
    · <span style="color:#16a34a">PK</span> primary key
  </p>
  <div class="layout">
    <div>{svg}</div>
    <div>
      <table>
        <tbody>
          {''.join(table_rows)}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""


def _node_key(node: TableNode) -> str:
    return f"{node.schema}.{node.name}" if node.schema else node.name


def _escape(s: str) -> str:
    """Minimal HTML escape."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


__all__ = ["JoinEdge", "JoinGraph", "TableNode", "build_join_graph", "render_html"]