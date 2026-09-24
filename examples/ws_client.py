"""Example WebSocket client for the /v1/ask streaming endpoint.

Prerequisites:
    1. The SpeakSQL service must be running:
         uv run uvicorn speaksql.service:app --host 127.0.0.1 --port 8765
    2. Install the websockets library:
         uv pip install websockets

Run:
    uv run python examples/ws_client.py "monthly amount by country"

Or import and call `stream(question, ...)` from your own code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "Missing dependency: install with `uv pip install websockets`\n"
    )
    sys.exit(1)


async def stream(
    question: str,
    *,
    url: str = "ws://127.0.0.1:8765/v1/ask",
    dialects: tuple[str, ...] = ("postgres", "bigquery", "duckdb"),
    is_sql: bool = False,
) -> AsyncIterator[dict]:
    """Yield each frame the server emits for a single question."""
    async with websockets.connect(url) as ws:
        await ws.send(
            json.dumps(
                {
                    "question": question,
                    "dialects": list(dialects),
                    "is_sql": is_sql,
                }
            )
        )
        async for raw in ws:
            frame = json.loads(raw)
            yield frame
            if frame.get("type") in ("done", "error"):
                return


async def _print_frames(question: str, url: str, dialects: tuple[str, ...]) -> None:
    print(f"→ {question}\n")
    async for frame in stream(question, url=url, dialects=dialects):
        kind = frame.get("type", "?")
        if kind == "llm_token":
            sys.stdout.write(frame.get("delta", ""))
            sys.stdout.flush()
        elif kind == "canonical":
            sys.stdout.write("\n\n--- canonical ---\n")
            sys.stdout.write(frame.get("sql", "") + "\n")
        elif kind == "transpile":
            dialect = frame.get("dialect", "?")
            sql = frame.get("sql", "")
            sys.stdout.write(f"\n--- {dialect} ---\n{sql}\n")
        elif kind == "done":
            sys.stdout.write("\n✓ done\n")
        elif kind == "error":
            sys.stdout.write(f"\n✗ error: {frame.get('message', '')}\n")
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stream NL→canonical→dialects from SpeakSQL /v1/ask"
    )
    parser.add_argument("question", help="Natural-language question or canonical SQL")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/v1/ask")
    parser.add_argument(
        "--dialect",
        action="append",
        dest="dialects",
        help="Target dialect (repeatable). Defaults to postgres, bigquery, duckdb.",
    )
    args = parser.parse_args(argv)

    dialects = (
        tuple(args.dialects) if args.dialects else ("postgres", "bigquery", "duckdb")
    )
    asyncio.run(_print_frames(args.question, args.url, dialects))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
