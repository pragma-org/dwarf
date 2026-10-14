from __future__ import annotations

import json

from profile_manager import profiles


def test_runtime_root_resolves_controller_current_generation(tmp_path, monkeypatch):
    base = tmp_path / "topology-runtimes"
    current = base / "profile-s-cardano-measurement-stock-control" / "current"
    current.mkdir(parents=True)
    (current / "runtime.json").write_text(json.dumps({"schema_version": "v1"}))
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(base))

    assert profiles.runtime_root(
        "profile-s-cardano-measurement-stock-control"
    ) == current


def test_runtime_root_preserves_legacy_flat_profile_layout(tmp_path, monkeypatch):
    base = tmp_path / "cardano-profiles"
    flat = base / "profile-e-cardano-preview"
    flat.mkdir(parents=True)
    (flat / "runtime.json").write_text(json.dumps({"schema_version": "v1"}))
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(base))

    assert profiles.runtime_root("profile-e-cardano-preview") == flat
