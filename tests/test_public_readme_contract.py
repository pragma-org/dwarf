import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _slug(heading: str) -> str:
    value = re.sub(r"<[^>]+>", "", heading).strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return re.sub(r"[ ]+", "-", value)


def test_readme_has_newcomer_first_product_contract():
    text = _text()
    headings = {
        "# DWARF",
        "## What DWARF is",
        "## Quick start with Docker",
        "## Run a real-node test",
        "## How DWARF is organized",
        "## Measurement program",
        "## Results and evidence",
        "## Versions and prerequisites",
        "## Repository map",
        "## Validate and develop",
        "## Security, reporting, and claim limits",
        "## Project and documentation",
    }
    assert headings <= set(text.splitlines())
    assert "real `cardano-node` and Amaru processes" in text
    assert "does not simulate either node" in text
    assert "/learn/measurements" in text
    assert "/operate/measurements" in text
    assert "/operate/measurement-profiles" in text
    assert "/run" in text


def test_readme_quick_start_and_local_cli_are_current():
    text = _text()
    for command in (
        "git clone https://github.com/pragma-org/dwarf.git",
        "cd dwarf",
        "bash delivery/scripts/install.sh",
        "bash delivery/scripts/status.sh",
        "python3 dwarf/cardano-profile --help",
        "python3 dwarf/cardano-profile scenario validate --semantic "
        "dwarf/scenarios/client-example-cbor-decoding-cardano-patched.yaml",
        "pytest -q",
    ):
        assert command in text
    assert "Docker Compose v2" in text
    assert "http://127.0.0.1:8787/" in text


def test_readme_explains_catalogs_sources_and_evidence_boundaries():
    text = _text()
    for term in (
        "Scenario",
        "Target",
        "Deployment profile",
        "Primitive",
        "Measurement profile",
        "Stock",
        "External",
        "Patched",
        "Reserved",
        "Catalogued is not exercised",
        "private retained runtime bundles",
        "operator logs",
        "CBOR decoding",
        "Plutus VM",
        "Invalid mini-protocol",
        "Block application",
        "Restart and sync",
    ):
        assert term in text


def test_readme_drops_stale_scope_and_volatile_counts():
    text = _text()
    stale = (
        "~228 scenarios",
        "12 ready profiles",
        "Antithesis support is `cardano-node`-only right now",
        "Amaru remains a **local-only** target",
        "runs a catalog of ~228 scenarios",
    )
    for phrase in stale:
        assert phrase not in text
    assert not re.search(r"\b(?:228|239) scenarios\b", text)


def test_readme_relative_links_and_anchors_resolve():
    text = _text()
    anchors = {
        _slug(line.lstrip("# "))
        for line in text.splitlines()
        if line.startswith("#")
    }
    links = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text)
    assert links
    for target in links:
        target = target.strip().split(" ", 1)[0].strip("<>")
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("#"):
            assert target[1:] in anchors, target
            continue
        path_text, _, fragment = target.partition("#")
        assert (ROOT / path_text).exists(), target
        if fragment and path_text == "README.md":
            assert fragment in anchors, target


def test_readme_shell_blocks_parse_and_public_safety_holds():
    text = _text()
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)
    assert blocks
    for block in blocks:
        parsed = subprocess.run(
            ["bash", "-n"], input=block, text=True, capture_output=True
        )
        assert parsed.returncode == 0, parsed.stderr

    lowered = text.lower()
    forbidden = (
        "gainpalfam",
        "bench.",
        "git.gain",
        "/home/nigel",
        "cardano-box",
        "cyber-castellum",
        "v7-pragma",
    )
    for value in forbidden:
        assert value not in lowered
    assert "dwarf/dashboard/static/dwarf-logo.png" in text
    assert "PRAGMA" in text
