"""F91: the legacy profile-a helper config resolves against the installed layout.

``runtime_common.PROFILE_A_CONFIG`` hardcoded the host-devnet era: ``/opt/dwarf/cardano-profiles``,
``~/.local/bin/cardano-cli``. On a dashboard install the profile runtime lives under the controller
state and the cli is on PATH; the legacy paths are kept when they exist.
"""
from __future__ import annotations

from pathlib import Path

import shutil

from scripts import runtime_common as rc


def test_profile_config_resolves_to_the_installed_layout_when_the_legacy_root_is_absent(tmp_path, monkeypatch):
    import profile_manager.profiles as profiles
    monkeypatch.setattr(profiles, "runtime_root", lambda pid: tmp_path / "topology-runtimes" / pid / "current")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}")
    config = rc.resolve_profile_config("profile-a-haskell-peersharing-disabled", legacy_root=tmp_path / "nowhere")
    assert config.runtime_root == tmp_path / "topology-runtimes" / "profile-a-haskell-peersharing-disabled" / "current"
    assert config.env_root == config.runtime_root / "env"
    assert config.cardano_cli == Path("/usr/local/bin/cardano-cli")
    assert config.cardano_node == Path("/usr/local/bin/cardano-node")
    assert config.log_paths[-1] == config.runtime_root / "logs" / "node1" / "stdout.log"
    assert config.network_magic == "42"


def test_profile_config_keeps_the_legacy_layout_when_it_exists(tmp_path, monkeypatch):
    legacy = tmp_path / "opt" / "profile-a"
    (legacy / "env").mkdir(parents=True)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    config = rc.resolve_profile_config("profile-a-haskell-peersharing-disabled", legacy_root=legacy)
    assert config.runtime_root == legacy and config.env_root == legacy / "env"
    assert config.cardano_cli == Path("/home/dwarf/.local/bin/cardano-cli")  # legacy default when nothing resolves


def test_module_constant_is_built_by_the_resolver():
    assert isinstance(rc.PROFILE_A_CONFIG, rc.RuntimeConfig)
    assert rc.PROFILE_A_CONFIG.session == "cardano-profile-a"
