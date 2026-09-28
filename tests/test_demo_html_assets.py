"""Static integrity test for docs/demo.html — every local asset must exist.

The HTML page references asciinema-player JS/CSS from docs/static/. If
someone renames the static dir, moves the page, or deletes an asset,
the player breaks silently (404s only when the browser tries to fetch).
This test walks every local href/src in the page and verifies each
target file exists on disk.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
DEMO_HTML = DOCS_DIR / "demo.html"


class _AssetHarvester(HTMLParser):
    """Pull every href/src that points to a local file (not http/https)."""

    def __init__(self) -> None:
        super().__init__()
        self.local_refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        # Standard link/script refs
        attrs_to_check = ("href", "src", "data")
        # <object data="..."> embeds SVGs and external resources; same check.
        for attr in attrs_to_check:
            value = attr_map.get(attr)
            if not value:
                continue
            # Skip non-local references
            if re.match(r"^(https?:)?//", value) or value.startswith(("data:", "#", "mailto:")):
                continue
            self.local_refs.append(value)


@pytest.mark.skipif(
    not DEMO_HTML.exists(), reason="docs/demo.html not generated yet"
)
def test_demo_html_local_assets_exist():
    """Every local href/src in demo.html must resolve to a real file."""
    html = DEMO_HTML.read_text()
    parser = _AssetHarvester()
    parser.feed(html)

    missing: list[str] = []
    for ref in parser.local_refs:
        # Strip query string and fragment
        clean = ref.split("?")[0].split("#")[0]
        if not clean:
            continue
        target = (DOCS_DIR / clean).resolve()
        # Prevent path traversal
        assert str(target).startswith(str(DOCS_DIR.resolve())), (
            f"asset path escapes docs/: {ref}"
        )
        if not target.exists():
            missing.append(ref)

    assert not missing, (
        f"docs/demo.html references {len(missing)} missing local asset(s): "
        f"{missing[:5]}"
    )


def test_demo_html_static_dir_inventory():
    """The docs/static/ dir must contain the player JS + CSS."""
    static = DOCS_DIR / "static"
    assert static.exists(), "docs/static/ missing"
    for name in ("asciinema-player.min.js", "asciinema-player.css"):
        assert (static / name).exists(), f"missing docs/static/{name}"


@pytest.mark.parametrize(
    "svg_name",
    [
        "feature_poster.svg",
        "ws_demo_mockup.svg",
        "join_graph_inline.svg",
        "demo.svg",
        "join_graph_example/join_graph.svg",
    ],
)
def test_svg_visuals_are_valid_xml(svg_name):
    """Every SVG referenced from demo.html must be well-formed XML."""
    import xml.etree.ElementTree as ET

    p = DOCS_DIR / svg_name
    if not p.exists():
        pytest.skip(f"{p} not present")
    try:
        ET.parse(p)
    except ET.ParseError as e:
        pytest.fail(f"{p} is malformed SVG: {e}")


def test_demo_html_itself_exists():
    """Sanity: the page must be committed to the repo."""
    assert DEMO_HTML.exists(), f"missing {DEMO_HTML}"
    assert DEMO_HTML.stat().st_size > 100, "demo.html suspiciously small"
