"""Public source must not ship live operator state or credential files."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tracked_paths():
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode()
    return [path for path in output.split("\0") if path]


def test_public_source_excludes_operator_state_and_bundles():
    forbidden = (
        "dwarf/state/", "dwarf/runs/", "dwarf/bundles/", "dwarf/evidence/",
        "docs/overhaul/proofs/", ".serena/", ".worktrees/",
    )
    offenders = [path for path in _tracked_paths() if path.startswith(forbidden)]
    assert not offenders, f"{len(offenders)} operational files: {offenders[:10]}"


def test_public_source_excludes_private_key_and_environment_files():
    offenders = [
        path for path in _tracked_paths()
        if path.endswith(".skey")
        or Path(path).name in {".env", "id_rsa", "id_ed25519", "controller-token", "dashboard-token"}
    ]
    assert not offenders, offenders
