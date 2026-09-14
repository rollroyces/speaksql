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
def ask(
    question: str | None,
    dialects: tuple[str, ...],
    is_sql: bool,
    as_json: bool,
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
        click.echo(json.dumps(out, indent=2))
        return

    for d, sql in out.items():
        click.echo(f"-- {d} --" + ("-" * max(0, 60 - len(d) - 6)))
        click.echo(sql)


@main.command()
def dialects() -> None:
    """Print all supported dialects."""
    for d in sorted(SUPPORTED_DIALECTS):
        click.echo(d)


if __name__ == "__main__":  # pragma: no cover
    main()