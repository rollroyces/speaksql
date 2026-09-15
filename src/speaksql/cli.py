"""SpeakSQL CLI: `speaksql "show me last month's revenue by region"`.

Reads an NL question (or canonical SQL), optionally links it to a known
schema, and emits per-dialect SQL. By default prints to stdout.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable

import click

from speaksql import SUPPORTED_DIALECTS, transpile
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
        "(sqlite, duckdb), point at this path instead of :memory:."
    ),
)
def ask(
    question: str | None,
    dialects: tuple[str, ...],
    is_sql: bool,
    as_json: bool,
    execute_on: str | None,
    db_path: str | None,
) -> None:
    """Translate QUESTION (NL or canonical SQL) into target-dialect SQL.

    With no QUESTION, read from stdin.
    """
    if question is None:
        question = sys.stdin.read().strip()
    if not question:
        raise click.UsageError("No question provided (arg or stdin).")

    canonical = question if is_sql else nl_to_canonical(question)
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
def dialects() -> None:
    """Print all supported dialects."""
    for d in sorted(SUPPORTED_DIALECTS):
        click.echo(d)


if __name__ == "__main__":  # pragma: no cover
    main()