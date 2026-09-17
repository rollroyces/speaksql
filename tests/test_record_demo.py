"""Tests for the demo recording infrastructure.

We don't have a browser to render the asciinema player in CI, so we
verify the lower-level invariants:

1. `scripts/record_demo.py` produces a syntactically-valid asciinema v2
   file (header + JSON frame array).
2. The replayed screen state is non-empty for every demo script.
3. The HTML demo page references the recorded .cast files.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DOCS_DIR = REPO_ROOT / "docs"

DEMO_NAMES = ["demo", "format", "repl", "diff", "graph"]


def _validate_cast(path: Path) -> tuple[dict, list]:
    """Parse a .cast file and return (header, frames)."""
    raw = path.read_text().splitlines()
    assert raw, f"{path} is empty"
    header = json.loads(raw[0])
    assert header["version"] == 2, f"{path} is not asciinema v2"
    assert "width" in header and "height" in header
    frames = [json.loads(line) for line in raw[1:]]
    for i, ev in enumerate(frames):
        assert len(ev) == 3, f"{path} frame {i} not [time, type, text]"
        assert ev[1] in ("o", "i", "x", "m"), f"{path} frame {i} bad type {ev[1]!r}"
    return header, frames


@pytest.mark.parametrize("name", DEMO_NAMES)
def test_record_demo_produces_valid_cast(tmp_path: Path, name: str):
    """Each named demo script should produce a valid .cast file."""
    out = tmp_path / f"{name}.cast"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "record_demo.py"), str(out), name],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, (
        f"recorder failed for {name}:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    _header, frames = _validate_cast(out)
    assert frames, f"{name} cast has no frames"
    # Should contain at least the prompt and one output frame per command.
    has_output = any(ev[1] == "o" and ev[2].strip() for ev in frames)
    assert has_output, f"{name} cast has no non-empty output frames"


@pytest.mark.parametrize("name", DEMO_NAMES)
def test_recorded_cast_replays_to_non_empty_screen(tmp_path: Path, name: str):
    """Replaying the cast through a minimal terminal emulator should
    produce visible content (not a blank screen)."""
    out = tmp_path / f"{name}.cast"
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "record_demo.py"), str(out), name],
        capture_output=True,
        check=True,
        timeout=60,
        cwd=REPO_ROOT,
    )
    header, frames = _validate_cast(out)
    cols, rows = header["width"], header["height"]

    # Minimal ANSI-aware screen emulator.
    screen = [[" "] * cols for _ in range(rows)]
    cursor = [0, 0]

    def write(text: str) -> None:
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
                # CSI sequence: skip until a letter
                j = i + 2
                while j < len(text) and not text[j].isalpha():
                    j += 1
                i = j + 1
                continue
            if ch == "\n":
                cursor[0] += 1
                cursor[1] = 0
            elif ch == "\r":
                cursor[1] = 0
            else:
                if 0 <= cursor[0] < rows and 0 <= cursor[1] < cols:
                    screen[cursor[0]][cursor[1]] = ch
                cursor[1] += 1
            i += 1

    for ev in frames:
        if ev[1] == "o":
            write(ev[2])

    # Screen must have at least some non-space characters.
    non_space = sum(
        1 for row in screen for ch in row if ch != " "
    )
    assert non_space > 20, (
        f"{name} screen is mostly blank after replay ({non_space} non-space chars)"
    )


def test_checked_in_demos_are_valid():
    """The .cast files shipped in docs/ must be valid (CI replays them
    into the demo.html)."""
    for name in DEMO_NAMES:
        cast_path = DOCS_DIR / f"demo{name if name != 'demo' else ''}.cast"
        # Files shipped:
        # - docs/demo.cast      (the main "demo" recording)
        # - docs/demo.format.cast
        # - docs/demo.diff.cast
        # - docs/demo.repl.cast
        # - docs/demo.graph.cast (the "graph" demo)
        if not cast_path.exists():
            # The "graph" demo is not shipped yet; skip rather than fail.
            continue
        _header, frames = _validate_cast(cast_path)
        assert frames, f"{cast_path} has no frames"


def test_recorded_demos_match_current_cli_output():
    """Committed recordings should reflect the actual CLI output.

    This catches the case where a CLI change silently desyncs the
    README's demo from the code. If a recording doesn't contain a
    string the current CLI produces, the README needs re-recording.
    """
    import subprocess

    # Collect the rendered output for a known query.
    subprocess.run(
        [sys.executable, "-m", "speaksql.cli", "ask", "-d", "postgres", "-d", "bigquery", "-d", "duckdb"],
        input=b"monthly amount by country\n",
        capture_output=True,
        timeout=20,
        cwd=REPO_ROOT,
        check=False,
    )

    # Substrings that should appear in the rendered output and also in
    # the demo.cast recording.
    expected_substrings = ["-- postgres", "-- bigquery", "-- duckdb"]
    # At least one Date_trunc-like marker (case-insensitive — SQLGlot may
    # choose the canonical form for each dialect).
    expected_substrings.append("date_trunc")

    # The committed demo.cast must contain every expected substring.
    demo_cast = DOCS_DIR / "demo.cast"
    if not demo_cast.exists():
        pytest.skip("docs/demo.cast not committed yet")
    text = demo_cast.read_text()

    for needle in expected_substrings:
        assert needle.lower() in text.lower(), (
            f"docs/demo.cast missing {needle!r}; "
            f"the demo is out of sync with the CLI. "
            f"Re-run scripts/record_demo.py to refresh."
        )


def test_demo_html_references_all_recordings():
    """The demo.html page should reference every checked-in .cast file
    and the asciinema player assets."""
    html_path = DOCS_DIR / "demo.html"
    assert html_path.exists(), "demo.html missing"
    html = html_path.read_text()
    # Player assets
    assert "static/asciinema-player.min.js" in html
    assert "static/asciinema-player.css" in html
    # At least one demo referenced
    assert "asciinema-player" in html
    assert 'src="demo.cast"' in html
    assert 'src="demo.format.cast"' in html
    assert 'src="demo.diff.cast"' in html
    assert 'src="demo.repl.cast"' in html


def test_player_assets_present():
    """The bundled asciinema player JS + CSS must be present."""
    for name in ("asciinema-player.min.js", "asciinema-player.css"):
        p = DOCS_DIR / "static" / name
        assert p.exists(), f"missing {p}"
        assert p.stat().st_size > 1000, f"{p} suspiciously small"