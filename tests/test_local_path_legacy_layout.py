"""F68–F73: the local path on a dashboard install must not depend on the legacy host layout.

Campaign 2026-11-08 (deploy bfa87d7c) failed on: scenario helper scripts and runtime roots
declared as ``~/dwarf-fw/scripts/...`` / ``~/cardano-profiles/<profile>/...`` (host layout,
absent inside the controller); two fault primitives referencing a ``handle`` that was not in
scope; cardano-node < 11 refusing a public-network configuration without ``TurnOnLogMetrics``
(the compatibility step ran only on the custom-testnet branch); and a failed post-``up``
verification leaving containers and an attached controller behind.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from profile_manager import primitives
from profile_manager.config import storage_roots
from profile_manager.profiles import runtime_root as profile_runtime_root
from scripts import runtime_compose_substrate as compose

DWARF_ROOT = primitives.DWARF_ROOT


class _Handle:
    def __init__(self, run_dir, *, prepared=None, profile_id=None):
        self.run_dir = run_dir
        self.logs = []
        self._profile_id = profile_id
        if prepared is not None:
            self._prepared_runtime_metadata_path = str(prepared)

    def log(self, **entry):
        self.logs.append(entry)


def _runtime(tmp_path, port=3101):
    body = {
        "haskell_nodes": [{
            "id": "node1", "name": "node1", "impl": "cardano-node", "port": port,
            "listen_address": f"127.0.0.1:{port}", "container_ip": "10.9.0.2",
            "container_listen_address": "node1:3001", "pid_file": str(tmp_path / "node1.pid"),
        }],
        "amaru_nodes": [], "nodes": [],
    }
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --- legacy host-layout paths resolve to the installed layout -------------------------------

@pytest.mark.parametrize("legacy", [
    "/home/dwarf/dwarf-fw/scripts/runtime_fetch_check.py",
    "~/dwarf-fw/scripts/runtime_fetch_check.py",
])
def test_legacy_repo_script_paths_map_to_the_installed_scripts_dir(legacy):
    assert primitives._resolve_runtime_path(legacy) == DWARF_ROOT / "scripts" / "runtime_fetch_check.py"


def test_legacy_repo_runs_dir_maps_to_the_storage_runs_root():
    assert primitives._resolve_runtime_path("~/dwarf-fw/runs") == storage_roots().runs
    assert primitives._resolve_runtime_path("/home/dwarf/dwarf-fw/runs/abc") == storage_roots().runs / "abc"


def test_legacy_profile_roots_map_to_the_profile_runtime_root():
    assert primitives._resolve_runtime_path(
        "~/cardano-profiles/profile-i-generated-haskell3/runtime.json"
    ) == profile_runtime_root("profile-i-generated-haskell3") / "runtime.json"
    assert primitives._resolve_runtime_path(
        "/opt/dwarf/cardano-profiles/profile-d-amaru-preview-proof"
    ) == profile_runtime_root("profile-d-amaru-preview-proof")


def test_existing_paths_are_left_alone(tmp_path):
    present = tmp_path / "dwarf-fw" / "scripts" / "x.py"
    present.parent.mkdir(parents=True)
    present.write_text("", encoding="utf-8")
    assert primitives._resolve_runtime_path(str(present)) == present


def test_every_helper_script_default_resolves_to_an_installed_script():
    missing = []
    for name in dir(primitives):
        default = getattr(getattr(primitives, name), "_DEFAULT_HELPER", None)
        if isinstance(default, str):
            resolved = primitives._resolve_runtime_path(default)
            if not resolved.is_file():
                missing.append((name, default, str(resolved)))
    assert missing == []


def test_no_primitive_reads_a_helper_script_or_runtime_root_without_the_resolver():
    source = Path(primitives.__file__).read_text(encoding="utf-8")
    assert 'str(self.params.get("helper_script", self._DEFAULT_HELPER))' not in source
    assert 'str(self.params["runtime_root"])' not in source


# --- F68: fault primitives must thread the handle -------------------------------------------

def test_port_drop_rules_read_the_target_port_through_the_handle(tmp_path):
    runtime = _runtime(tmp_path, port=3177)
    prim = primitives.FaultLocalPortDrop(params={
        "target_node": "node1",
        "runtime_metadata_path": "~/cardano-profiles/profile-x/runtime.json",
    })
    port, _host, _protocol, rules = prim._rules(_Handle(tmp_path, prepared=runtime))
    assert port == 3177
    assert rules


def test_node_freeze_pid_resolution_reads_the_node_through_the_handle(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    (tmp_path / "node1.pid").write_text("4242\n", encoding="utf-8")
    prim = primitives.FaultNodeFreeze(params={"target_node": "node1"})
    monkeypatch.setattr(prim, "_candidate_pid_matches", lambda *, pid, node: pid == 4242)
    assert prim._resolve_target_pid(_Handle(tmp_path, prepared=runtime)) == 4242


# --- shell steps get the installed layout through the environment ---------------------------

def test_shell_env_exposes_the_installed_scripts_dir_and_the_prepared_profile_root(tmp_path):
    runtime = _runtime(tmp_path)
    env = primitives._build_dwarf_telemetry_env(_Handle(tmp_path, prepared=runtime))
    assert str(DWARF_ROOT / "scripts") in env["PYTHONPATH"].split(os.pathsep)
    assert env["DWARF_SCRIPTS_DIR"] == str(DWARF_ROOT / "scripts")
    assert env["DWARF_RUNTIME_METADATA_PATH"] == str(runtime)
    assert env["DWARF_PROFILE_RUNTIME_ROOT"] == str(tmp_path)


def test_shell_env_without_a_prepared_runtime_still_exposes_the_scripts_dir(tmp_path):
    env = primitives._build_dwarf_telemetry_env(_Handle(tmp_path))
    assert env["DWARF_SCRIPTS_DIR"] == str(DWARF_ROOT / "scripts")
    assert "DWARF_RUNTIME_METADATA_PATH" not in env


# --- F70: cardano config policies apply on every compose branch -----------------------------

def test_public_network_cardano_config_gets_the_version_policies(tmp_path):
    config = tmp_path / "configuration.yaml"
    config.write_text(json.dumps({"Protocol": "Cardano", "TraceOptions": {}}) + "\n", encoding="utf-8")
    report = compose._apply_cardano_config_policies(
        config_path=config,
        nodes=[{"id": "node1", "impl": "cardano-node", "version": "10.7.1"}],
        explicit=None,
    )
    body = json.loads(config.read_text(encoding="utf-8"))
    assert body["TurnOnLogMetrics"] is True
    assert report["compatibility"]["required_by_versions"] == ["10.7.1"]
    # the interop pin is a policy decision: it applies only to cardano >= 11.1 next to Amaru
    assert report["experimental_protocols"]["pinned"] is False
    assert report["experimental_protocols"]["cardano_versions"] == ["10.7.1"]


def test_public_network_mixed_config_gets_the_interop_pin_too(tmp_path):
    config = tmp_path / "configuration.yaml"
    config.write_text(json.dumps({"Protocol": "Cardano", "TraceOptions": {}}) + "\n", encoding="utf-8")
    report = compose._apply_cardano_config_policies(
        config_path=config,
        nodes=[
            {"id": "node1", "impl": "cardano-node", "version": "11.1.2"},
            {"id": "node2", "impl": "amaru", "version": "10.11.20260925"},
        ],
        explicit=None,
    )
    body = json.loads(config.read_text(encoding="utf-8"))
    assert body["ExperimentalProtocolsEnabled"] is False
    assert "TurnOnLogMetrics" not in body
    assert report["experimental_protocols"]["pinned"] is True


def test_every_compose_branch_that_writes_a_cardano_config_applies_the_policies():
    source = Path(compose.__file__).read_text(encoding="utf-8")
    # custom testnet, docker public network, multihost public network
    assert source.count("_apply_cardano_config_policies(") >= 4  # definition + three call sites


# --- F73: a failed post-up verification must not leave the substrate behind -----------------

def test_failed_post_up_verification_tears_the_substrate_down_and_detaches_the_controller(tmp_path):
    calls = []

    def runner(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    compose._cleanup_failed_substrate(
        docker_project="dwarf-substrate-x",
        runtime_root=tmp_path,
        controller_attachment={"container": "abc", "network": "dwarf-substrate-x-net"},
        runner=runner,
    )
    disconnect = ["docker", "network", "disconnect", "dwarf-substrate-x-net", "abc"]
    down = ["docker", "compose", "--project-name", "dwarf-substrate-x", "down", "--volumes", "--remove-orphans"]
    assert disconnect in calls and down in calls
    assert calls.index(disconnect) < calls.index(down)


def test_failed_verification_cleanup_runs_without_an_attachment(tmp_path):
    calls = []

    def runner(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    report = compose._cleanup_failed_substrate(
        docker_project="dwarf-substrate-y", runtime_root=tmp_path, controller_attachment=None, runner=runner
    )
    assert [c[:3] for c in calls] == [["docker", "compose", "--project-name"]]
    assert report["down_ok"] is False


def test_docker_compose_verification_is_wrapped_in_the_cleanup():
    source = Path(compose.__file__).read_text(encoding="utf-8")
    up = source.index('"up", "-d"')
    assert "_cleanup_failed_substrate(" in source[up:]
