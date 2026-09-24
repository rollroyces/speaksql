"""Eval harness for the SpeakSQL NL → canonical SQL planner.

Run:

    PYTHONPATH=src python examples/llm_eval/run.py [--provider mock|openai]

Eachcase in eval_set.jsonl is one JSON line:

    {"input": "count of orders", "expected": "SELECT COUNT(orders) AS cnt FROM events"}

The harness:
1. Loads each case from eval_set.jsonl
2. Calls `nl_to_canonical(input)` (with the configured provider)
3. Parses both `expected` and the model's output via SQLGlot
4. Compares canonical ASTs (text equivalence after canonicalization)

Exit code: 0 if all cases pass, 1 otherwise. Prints a summary table.

This is intentionally tiny — no external eval library, no ML framework.
It's a regression test, not a leaderboard. Use it to detect breakage
when upgrading SQLGlot, changing the prompt, or swapping providers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import errors as _sqlglot_errors

# Allow running from repo root without installing.
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@dataclass
class Case:
    input: str
    expected: str


@dataclass
class Result:
    case: Case
    actual: str
    passed: bool
    note: str = ""


def load_cases(path: Path) -> Iterable[Case]:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        yield Case(input=obj["input"], expected=obj["expected"])


def ast_equal(expected: str, actual: str) -> tuple[bool, str]:
    """Parse both SQL strings with SQLGlot; compare canonical ASTs."""
    try:
        a = sqlglot.parse_one(expected)
        b = sqlglot.parse_one(actual)
    except (_sqlglot_errors.ParseError, _sqlglot_errors.TokenError) as e:
        return False, f"parse error: {e}"
    if a is None or b is None:
        return False, "could not parse"
    if a.sql() == b.sql():
        return True, ""
    return False, f"canonical AST differs:\n  expected: {a.sql()!r}\n  actual:   {b.sql()!r}"


def evaluate(cases: Iterable[Case]) -> list[Result]:
    from speaksql.nl import nl_to_canonical

    results: list[Result] = []
    for c in cases:
        try:
            actual = nl_to_canonical(c.input)
        except Exception as e:  # noqa: BLE001 — eval records all failures as Result rows
            results.append(Result(c, "", False, f"exception: {e}"))
            continue
        passed, note = ast_equal(c.expected, actual)
        results.append(Result(c, actual, passed, note))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="SpeakSQL NL planner eval harness")
    parser.add_argument(
        "--provider",
        choices=("mock", "openai"),
        default="mock",
        help="LLM provider to evaluate against (default: mock)",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("eval_set.jsonl"),
        help="Path to eval set (default: ./eval_set.jsonl)",
    )
    args = parser.parse_args()

    if args.provider == "openai":
        if not os.environ.get("SPEAKSQL_LLM_BASE_URL"):
            print("error: --provider openai requires SPEAKSQL_LLM_BASE_URL", file=sys.stderr)
            return 2
        os.environ.setdefault("SPEAKSQL_LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))

    os.environ.setdefault("SPEAKSQL_USE_LLM", "1")

    cases = list(load_cases(args.cases))
    if not cases:
        print(f"error: no cases found in {args.cases}", file=sys.stderr)
        return 2

    results = evaluate(cases)
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    print(f"\nSpeakSQL NL planner eval — provider: {args.provider}\n")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.case.input!r}")
        if not r.passed:
            print(f"        expected: {r.case.expected!r}")
            print(f"        actual:   {r.actual!r}")
            if r.note:
                print(f"        note:     {r.note}")
    print(f"\n{passed}/{total} cases passed.\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())