"""SpeakSQL CLI: `speaksql "show me last month's revenue by region"`.

Reads an NL question (or canonical SQL), optionally links it to a known
schema, and emits per-dialect SQL. By default prints to stdout.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path

import click

from speaksql import SUPPORTED_DIALECTS, format_diff, semantic_diff, transpile
from speaksql.nl import nl_to_canonical  # optional NL layer


@click.group()
@click.version_option(package_name="speaksql")
def main() -> None:
    """SpeakSQL — speak once, query any dialect."""


@main.command()
@click.argument("question", required=False)
@click.option(
    "-d",
    "--dialect",
    "dialects",
    multiple=True,
    type=click.Choice(sorted(SUPPORTED_DIALECTS)),
    help="Target dialect(s). Repeat for multiple. Default: all.",
)
@click.option(
    "--sql",
    "is_sql",
    is_flag=True,
    help="Treat QUESTION as canonical ANSI SQL instead of NL.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a JSON map {dialect: sql} instead of pretty blocks.",
)
@click.option(
    "--execute-on",
    "--execute",
    "execute_on",
    type=click.Choice(sorted({"sqlite", "duckdb", "postgres", "snowflake", "bigquery"})),
    default=None,
    help=(
        "If set, execute the transpiled SQL on this backend after printing. "
        "Only useful when a single target dialect matches (e.g. --execute-on duckdb "
        "with --dialect duckdb)."
    ),
)
@click.option(
    "--db-path",
    "db_path",
    default=None,
    help=(
        "For backends that take a connection string / file path "
        "(sqlite, duckdb), point at this path instead of :memory:. "
        "When set, also enables FK-aware SQL generation against the schema."
    ),
)
@click.option(
    "--instructions",
    "instructions_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help=(
        "Path to a file with per-source instructions appended to the LLM "
        "system prompt (e.g. 'always use lowercase aliases', 'treat null "
        "amounts as zero'). Plain text; one paragraph is enough."
    ),
)
@click.option(
    "--examples",
    "examples_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help=(
        "Path to a JSONL example-query library. Each line is an object with "
        "'question' and 'sql' fields; top-K matches by BM25 are injected "
        "as few-shot examples into the LLM system prompt."
    ),
)
def ask(
    question: str | None,
    dialects: tuple[str, ...],
    is_sql: bool,
    as_json: bool,
    execute_on: str | None,
    db_path: str | None,
    instructions_path: Path | None,
    examples_path: Path | None,
) -> None:
    """Translate QUESTION (NL or canonical SQL) into target-dialect SQL.

    With no QUESTION, read from stdin.
    """
    if question is None:
        question = sys.stdin.read().strip()
    if not question:
        raise click.UsageError("No question provided (arg or stdin).")

    # FK-aware SQL generation: if --db-path is given AND we're in NL
    # mode, introspect the schema + FKs and pass them to nl_to_canonical.
    schema = None
    if db_path is not None and not is_sql:
        schema = _introspect_for_path(db_path)

    # Per-source instructions + example library flow into the LLM
    # system prompt only when the LLM is actually invoked. The rule
    # layer (nl_to_canonical) doesn't see them; that's by design —
    # the rules are deterministic and don't need hints.
    instructions: str | None = None
    if instructions_path is not None:
        instructions = instructions_path.read_text(encoding="utf-8").strip() or None

    examples: list[tuple[str, str]] | None = None
    if examples_path is not None:
        from speaksql.examples import (
            BM25Retriever,
            load_examples_from_jsonl,
        )

        lib = load_examples_from_jsonl(examples_path)
        hits = BM25Retriever().retrieve(question, lib, top_k=5)
        examples = [(h.example.question, h.example.sql) for h in hits] or None

    canonical = question if is_sql else nl_to_canonical(
        question,
        schema=schema,
        instructions=instructions,
        examples=examples,
    )
    targets: Iterable[str] = dialects or sorted(SUPPORTED_DIALECTS)
    out = transpile(canonical, targets)

    if as_json:
        payload = {"canonical": canonical, "results": out}
        if execute_on is not None:
            payload["execution"] = _maybe_execute(execute_on, out, dialects, db_path)
        click.echo(json.dumps(payload, indent=2, default=_json_default))
        return

    for d, sql in out.items():
        click.echo(f"-- {d} --" + ("-" * max(0, 60 - len(d) - 6)))
        click.echo(sql)

    if execute_on is not None:
        click.echo("")
        click.echo(f"=== Execution on {execute_on} ===")
        result = _maybe_execute(execute_on, out, dialects, db_path)
        click.echo(json.dumps(result, indent=2, default=_json_default))


def _introspect_for_path(db_path: str):
    """Best-effort introspect for CLI's --db-path. Returns None on failure.

    Used to enable FK-aware SQL generation: when --db-path points at a
    SQLite or DuckDB file, we read its schema + declared FKs and pass
    them to nl_to_canonical as `schema=`.
    """
    from pathlib import Path

    from speaksql.backends import backend_for

    p = Path(db_path)
    if not p.exists():
        return None
    b = "duckdb" if p.suffix.lower() == ".duckdb" else "sqlite"
    try:
        be = backend_for(b, path=str(p))
        schema = be.introspect()
        fks = be.foreign_keys()
        be.close()
        return schema.with_foreign_keys(fks)
    except (OSError, RuntimeError, ValueError, TypeError):
        # Driver missing, connection refused, malformed file — any of
        # these means the FK-aware path can't proceed. Return None and
        # let the CLI fall back to the default schema-agnostic rules.
        return None


def _json_default(obj: object) -> str:
    """Fallback JSON encoder for DB-specific types (datetime, date, Decimal...)."""
    # Common DB types: datetime, date, time, Decimal, UUID, bytes
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "as_tuple"):  # Decimal
        return float(obj)
    return str(obj)


def _maybe_execute(
    backend_name: str,
    out: dict[str, str],
    requested_dialects: tuple[str, ...],
    db_path: str | None,
) -> dict[str, object]:
    """Execute the transpiled SQL against a backend, if applicable.

    The backend's natural dialect must be among the requested targets,
    otherwise there's nothing meaningful to run.
    """
    from speaksql.backends import backend_for
    from speaksql.exceptions import BackendError, SpeakSQLError

    target_dialects = set(requested_dialects) if requested_dialects else set(out)
    if backend_name not in target_dialects:
        return {
            "executed": False,
            "reason": f"backend '{backend_name}' was not a transpile target",
        }
    if backend_name not in out:
        return {
            "executed": False,
            "reason": f"no SQL emitted for backend '{backend_name}'",
        }

    sql_to_run = out[backend_name]
    kwargs: dict[str, object] = {}
    if db_path is not None and backend_name in ("sqlite", "duckdb"):
        kwargs["path"] = db_path
    try:
        be = backend_for(backend_name, **kwargs)
    except BackendError as e:
        return {"executed": False, "reason": str(e)}

    try:
        rows = be.execute(sql_to_run)
        return {
            "executed": True,
            "backend": backend_name,
            "row_count": len(rows),
            "rows": [list(r) for r in rows[:100]],  # cap at 100
        }
    except SpeakSQLError as e:
        return {"executed": False, "backend": backend_name, "error": str(e)}
    finally:
        be.close()


@main.command()
@click.argument("db_path")
@click.option(
    "--backend",
    "-b",
    type=click.Choice(sorted({"sqlite", "duckdb"})),
    default="sqlite",
    help="Which in-process backend to use (default: sqlite).",
)
@click.option(
    "--html",
    "html_out",
    type=click.Path(),
    default=None,
    help="Write the rendered HTML to this file (default: stdout SVG summary).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the graph as JSON instead of HTML/text.",
)
def graph(db_path: str, backend: str, html_out: str | None, as_json: bool) -> None:
    """Build a JOIN graph from a SQLite (or DuckDB) database file.

    Connects to the database, reads its schema + declared foreign keys,
    infers join candidates, and prints either JSON, text, or writes
    an HTML visualization.
    """
    from speaksql.backends import backend_for
    from speaksql.graph import build_join_graph, render_html

    if backend == "duckdb" and not __import__("importlib").util.find_spec("duckdb"):
        click.echo("duckdb backend not installed; pip install speaksql[duckdb]", err=True)
        raise click.exceptions.Exit(2)

    be = backend_for(backend, path=db_path)
    try:
        schema = be.introspect()
        fks = be.foreign_keys()
    finally:
        be.close()

    enriched = schema.with_foreign_keys(fks)
    g = build_join_graph(enriched)
    if as_json:
        import json as _json
        click.echo(_json.dumps(g.to_dict(), indent=2))
        return
    if html_out:
        Path(html_out).write_text(render_html(g, title=f"JOIN Graph — {db_path}"))
        click.echo(f"wrote {html_out}")
        return
    # Text fallback
    real = sum(1 for _ in fks)
    click.echo(f"{len(g.nodes)} tables · {len(g.edges)} inferred joins ({real} from declared FKs)")
    for e in sorted(g.edges, key=lambda e: (e.source, e.target)):
        click.echo(f"  {e.source} <-> {e.target}  via {', '.join(e.columns)}")


@main.command()
def dialects() -> None:
    """Print all supported dialects."""
    for d in sorted(SUPPORTED_DIALECTS):
        click.echo(d)


@main.command(name="format")
@click.argument(
    "sql",
    required=False,
)
@click.option(
    "-d",
    "--dialect",
    "dialect",
    type=click.Choice(sorted(SUPPORTED_DIALECTS)),
    default=None,
    help="Source dialect of the input (so SQLGlot can parse correctly). "
    "Defaults to canonical ANSI.",
)
@click.option(
    "--target",
    "target",
    type=click.Choice(sorted(SUPPORTED_DIALECTS)),
    default=None,
    help="Target dialect to emit. Defaults to the source dialect (or ANSI).",
)
@click.option(
    "--compact/--pretty",
    default=True,
    help="Compact (single-line, default) or pretty (multi-line indented) output.",
)
@click.option(
    "--from-file",
    "from_file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Read SQL from a file instead of the SQL argument.",
)
def format_cmd(
    sql: str | None,
    dialect: str | None,
    target: str | None,
    compact: bool,
    from_file: Path | None,
) -> None:
    """Pretty-print SQL (canonicalize then re-render).

    Use this to canonicalize ad-hoc SQL into a consistent shape, or to
    convert SQL from one dialect to another without running an LLM. If
    no SQL argument is given, the command reads from stdin so it works
    in pipelines:

    \b
        echo "select 1,2" | speaksql format
        cat query.sql | speaksql format --dialect postgres
        speaksql format "select 1,2" --target snowflake
    """
    import sys

    import sqlglot


    if from_file is not None:
        sql_text = from_file.read_text()
    elif sql is not None:
        sql_text = sql
    else:
        # Read from stdin if no argument and no file.
        sql_text = sys.stdin.read()

    sql_text = sql_text.strip()
    if not sql_text:
        raise click.BadParameter("empty SQL")

    src = dialect or ""
    dst = target or src
    try:
        # Use SQLGlot directly so we can pass the source dialect for
        # correct parsing (transpile() assumes canonical input).
        out = sqlglot.transpile(
            sql_text,
            read=src,
            write=dst,
            pretty=not compact,
        )[0]
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as exc:
        raise click.ClickException(f"failed to parse SQL: {exc}") from exc
    except Exception as exc:  # pragma: no cover — defensive
        raise click.ClickException(f"failed to format SQL: {exc}") from exc

    # Apply any vendor-specific overrides (DuckDB DATEDIFF etc.) so the
    # output is also normalized.
    from speaksql.vendor_overrides import apply_overrides

    out = apply_overrides(out, dst)

    # When compact, squeeze runs of whitespace to a single space (SQLGlot
    # already returns single-line for pretty=False).
    if compact:
        formatted = " ".join(out.split())
    else:
        formatted = out
    click.echo(formatted)


@main.command()
@click.argument("sql_a")
@click.argument("sql_b")
@click.option(
    "-d",
    "--dialect",
    "dialects",
    nargs=2,
    type=click.Choice(sorted(SUPPORTED_DIALECTS)),
    default=None,
    help="Source dialects for the two SQL strings (e.g. -d postgres bigquery).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a JSON list of differences instead of pretty text.",
)
def diff(
    sql_a: str,
    sql_b: str,
    dialects: tuple[str, str] | None,
    as_json: bool,
) -> None:
    """Show semantic differences between two SQL statements.

    Parses both SQL strings with SQLGlot (so dialect-aware parsing applies),
    then reports categorical differences: function-call rewrites, null-ordering,
    sort direction, type aliases, and structural differences.

    A zero-difference result means the two statements are semantically identical.
    """
    da = dialects[0] if dialects else ""
    db = dialects[1] if dialects else ""
    diffs = semantic_diff(sql_a, sql_b, dialect_a=da, dialect_b=db)
    if as_json:
        click.echo(json.dumps([d.to_dict() for d in diffs], indent=2))
        return
    click.echo(format_diff(diffs))


@main.command()
@click.option(
    "-d",
    "--dialect",
    multiple=True,
    help="Dialect(s) to emit on every turn. Can be passed multiple times. "
    "If omitted, all supported dialects are emitted.",
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="SQLite or DuckDB file to introspect for schema-aware REPL.",
)
def repl(dialect: tuple[str, ...], db_path: Path | None) -> None:
    """Interactive SpeakSQL REPL.

    Each line is treated as an NL question (or canonical SQL if it begins
    with SELECT/WITH/INSERT/UPDATE/DELETE/MERGE). Output is the per-dialect
    SQL for the active dialects. Type `:dialects` to switch, `:help` for
    commands, `:quit` to exit.
    """
    from speaksql.backends import backend_for
    from speaksql.exceptions import TranspileError
    from speaksql.schema_aware import joins_to_sql, plan_for_question

    _SQL_PREFIXES = ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "MERGE")

    active_dialects: list[str] = list(dialect) if dialect else list(SUPPORTED_DIALECTS)
    schema = None
    if db_path is not None:
        try:
            backend = backend_for("sqlite", path=str(db_path))
            schema = backend.introspect()
            click.echo(f"# introspected {len(schema.tables)} tables from {db_path}")
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            click.echo(f"# could not introspect {db_path}: {exc}", err=True)
            schema = None

    click.echo(
        f"SpeakSQL REPL — dialects: {', '.join(active_dialects)}\n"
        f"Type a question, ':help' for commands, ':quit' to exit."
    )
    try:
        while True:
            try:
                line = input("> ")
            except EOFError:
                click.echo("")
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith(":"):
                if _repl_command(line, active_dialects):
                    break
                continue
            try:
                is_sql = line.upper().startswith(_SQL_PREFIXES)
                if schema is not None and not is_sql:
                    # NL → canonical via FK-aware planner, then emit.
                    hint = plan_for_question(line, schema)
                    if hint.tables:
                        joins = joins_to_sql(hint.joins)
                        from_table = hint.tables[0]
                        canonical = f"SELECT * FROM {from_table}"
                        if joins:
                            canonical += "\n" + joins
                    else:
                        # No table match — fall back to raw transpile.
                        result = transpile(line, targets=tuple(active_dialects))
                        for d in active_dialects:
                            click.echo(f"--- {d} ---")
                            click.echo(result[d].rstrip())
                        continue
                    result = transpile(
                        canonical, targets=tuple(active_dialects)
                    )
                    for d in active_dialects:
                        click.echo(f"--- {d} ---")
                        click.echo(result[d].rstrip())
                else:
                    # Raw SQL (or no schema): transpile directly.
                    result = transpile(line, targets=tuple(active_dialects))
                    for d in active_dialects:
                        click.echo(f"--- {d} ---")
                        click.echo(result[d].rstrip())
            except (
                TranspileError,
                ValueError,
                TypeError,
                RuntimeError,
                OSError,
            ) as exc:
                click.echo(f"error: {exc}", err=True)
    except KeyboardInterrupt:
        click.echo("")
    click.echo("bye!")


def _repl_command(line: str, active_dialects: list[str]) -> bool:
    """Handle `:cmd` lines in the REPL.

    Returns True if the user asked to quit (so the caller can break the
    loop cleanly).
    """
    parts = line[1:].split()
    if not parts:
        return False
    cmd = parts[0]
    if cmd in ("quit", "exit", "q"):
        return True
    if cmd in ("dialects", "d"):
        if len(parts) > 1:
            active_dialects.clear()
            active_dialects.extend(parts[1:])
            click.echo(f"active dialects: {', '.join(active_dialects)}")
        else:
            click.echo(f"current: {', '.join(active_dialects)}")
    elif cmd in ("help", "h", "?"):
        click.echo("commands: :help, :dialects [...], :quit")
    else:
        click.echo(f"unknown: {line!r}; try :help")
    return False


if __name__ == "__main__":  # pragma: no cover
    main()