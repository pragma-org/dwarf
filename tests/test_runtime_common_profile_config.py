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


def test_long_socket_paths_are_reached_through_a_short_link(tmp_path, monkeypatch):
    """F94: a unix socket path may hold at most 107 bytes; the controller-side profile runtime path
    is longer, so cardano-cli failed with ``unixPathMax 108``. Long paths go through a short link."""
    deep = tmp_path / ("d" * 60) / ("e" * 60) / "socket" / "node1"
    deep.mkdir(parents=True)
    target = deep / "sock"
    target.write_text("", encoding="utf-8")
    import tempfile; monkeypatch.setattr(rc, "_SHORT_SOCKET_DIR", Path(tempfile.mkdtemp(prefix="ds", dir="/tmp")))
    short = rc.short_socket_path(target)
    assert len(str(short)) < 100 and short.resolve() == target.resolve()
    assert rc.short_socket_path(tmp_path / "x.sock") == tmp_path / "x.sock"  # short paths unchanged


def test_query_tip_passes_the_short_link_to_cardano_cli(tmp_path, monkeypatch):
    root = tmp_path / ("r" * 70) / ("q" * 40)
    sock = root / "socket" / "node1" / "sock"
    sock.parent.mkdir(parents=True); sock.write_text("", encoding="utf-8")
    import tempfile; monkeypatch.setattr(rc, "_SHORT_SOCKET_DIR", Path(tempfile.mkdtemp(prefix="ds", dir="/tmp")))
    seen = {}

    def fake_run(args, **kwargs):
        seen["socket"] = kwargs["env"]["CARDANO_NODE_SOCKET_PATH"]
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout='{"slot": 5, "block": 2, "syncProgress": "100.00", "hash": "ab"}', stderr="")

    monkeypatch.setattr(rc, "run", fake_run)
    config = rc.RuntimeConfig(runtime_root=root, env_root=root / "env", session="s", cardano_cli=Path("/bin/true"),
                              cardano_node=Path("/bin/true"), network_magic="42", blockfetch_bin="", chainsync_bin="",
                              log_paths=(), fallback_range=("", ""))
    assert rc.query_tip(config, "node1")["slot"] == 5
    assert len(seen["socket"]) < 100


def test_a_controller_profile_without_node_containers_refuses_to_restart(tmp_path, monkeypatch):
    """Run 20261109T040452Z (m3-runtime-profile-restart-tip-recovery): no node container was found, the
    helper fell back to the host-era tmux path, killed nothing, 'started' a session in 0.016 s and the
    scenario passed with an identical tip. On a controller install that must be an error, not a restart."""
    import pytest

    from scripts import runtime_common as rc

    root = tmp_path / "topology-runtimes" / "profile-a" / "current"
    root.mkdir(parents=True)
    (root / "runtime.json").write_text('{"profile_id": "profile-a", "nodes": []}', encoding="utf-8")
    config = rc.resolve_profile_config("profile-a", legacy_root=tmp_path / "no-legacy")
    config = config.__class__(**{**config.__dict__, "runtime_root": root, "env_root": root / "env"})
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: pytest.fail(f"must not run {a}"))
    with pytest.raises(RuntimeError, match="no node containers"):
        rc.stop_session_with_details(config)
    with pytest.raises(RuntimeError, match="no node containers"):
        rc.start_session_with_details(config)
