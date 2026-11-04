"""The embedded viewer catalog is generated from presentation-v1.json, never hand-edited."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "dwarf/scripts/render_viewer_presentation_catalog.py"
PAYLOAD = re.compile(r'<script type="application/json" data-portable-viewer-payload>(.*?)</script>', re.S)
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "dwarf")}


def _payload(path):
    return json.loads(PAYLOAD.search(path.read_text()).group(1))


def _copy(tmp_path):
    for name in ("docs/index.html", "index.html"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def _run(*args, check=True):
    return subprocess.run([sys.executable, str(SCRIPT), *args], check=check, capture_output=True, text=True, env=ENV)


def test_generator_is_idempotent_and_preserves_other_payload_keys(tmp_path):
    root = _copy(tmp_path)
    before = _payload(root / "docs/index.html")
    _run("--root", str(root))
    after = _payload(root / "docs/index.html")
    assert set(after) == set(before) and after["runs"] == before["runs"] and after["filters"] == before["filters"]
    catalog = after["presentation_catalog"]
    assert catalog["bindings"]["cardano-node"]["keepalive_rtt"][0]["concept"] == "keepalive-round-trip-time"
    assert catalog["concepts"]["chain-advance-count"]["compare_as"] is None
    assert catalog["concepts"]["adopted-block-count"]["compare_as"] == "chain-advance-count"
    assert catalog["unit_short"]["microseconds"] == "us" and catalog["implementation_labels"]["amaru"] == "Amaru"
    assert catalog["by_metric"]["block_application"]["comparison_direction"] == "lower"
    first = (root / "docs/index.html").read_text()
    _run("--root", str(root))
    second = (root / "docs/index.html").read_text()
    assert first.replace(after["generated_at_utc"], "") == second.replace(_payload(root / "docs/index.html")["generated_at_utc"], "")
    assert _payload(root / "index.html")["presentation_catalog"] == after["presentation_catalog"]


def test_check_mode_detects_hand_edits(tmp_path):
    root = _copy(tmp_path)
    _run("--root", str(root))
    assert _run("--root", str(root), "--check").returncode == 0
    text = (root / "docs/index.html").read_text().replace('"keepalive-round-trip-time"', '"keepalive-round-trip-tim"', 1)
    (root / "docs/index.html").write_text(text)
    result = _run("--root", str(root), "--check", check=False)
    assert result.returncode == 1 and "docs/index.html" in result.stdout + result.stderr


def test_committed_viewers_match_the_catalog():
    result = _run("--check", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
