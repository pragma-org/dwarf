"""Embed the measurement presentation catalog into the offline viewers.

``docs/index.html`` (and its root mirror ``index.html``) carry a
``data-portable-viewer-payload`` JSON whose ``presentation_catalog`` is the
viewer's measurement vocabulary.  This script renders that index from
``dwarf/measurements/presentation-v1.json`` through the same
``_catalog_index`` the Python comparison model uses, so the catalog has one
source.  ``--check`` reports drift without writing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from profile_manager.data.measurement_compare import _catalog_index

VIEWERS = ("docs/index.html", "index.html")
_PAYLOAD = re.compile(r'(<script type="application/json" data-portable-viewer-payload>)(.*?)(</script>)', re.S)
_CATALOG_KEYS = ("by_metric", "categories", "concepts", "bindings", "implementation_labels", "unit_short", "unit_full")


def render_payload_catalog(index: dict) -> dict:
    """The ``presentation_catalog`` value for the viewer payload."""
    return {key: index[key] for key in _CATALOG_KEYS}


def _split(text: str) -> tuple[str, dict, str, re.Match]:
    match = _PAYLOAD.search(text)
    if match is None:
        raise ValueError("viewer payload block not found")
    return match.group(1), json.loads(match.group(2)), match.group(3), match


def rewrite_viewer(path: Path, catalog: dict, *, now: str, check: bool = False) -> bool:
    """Replace the payload's catalog (and ``generated_at_utc``); return whether the file differs."""
    text = path.read_text(encoding="utf-8")
    opening, payload, closing, match = _split(text)
    current = payload.get("presentation_catalog")
    changed = current != catalog
    if check:
        return changed
    if not changed:
        return False
    payload["presentation_catalog"] = catalog
    payload["generated_at_utc"] = now
    # Inside a <script> block a literal "</" would end the element; JSON reads "<\/" back as "</".
    rendered = json.dumps(payload, separators=(", ", ": ")).replace("</", "<\\/")
    path.write_text(text[: match.start()] + opening + rendered + closing + text[match.end():], encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2], help="repository root holding the viewers")
    parser.add_argument("--check", action="store_true", help="exit 1 when a viewer's embedded catalog differs from the rendered one")
    args = parser.parse_args(argv)
    catalog = render_payload_catalog(_catalog_index())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    drift = []
    for name in VIEWERS:
        path = args.root / name
        if rewrite_viewer(path, catalog, now=now, check=args.check):
            drift.append(name)
            print(("drift: " if args.check else "updated: ") + name)
    if args.check and drift:
        print("embedded viewer catalog differs from presentation-v1.json; run dwarf/scripts/render_viewer_presentation_catalog.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
