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


# --- F74: a declared runtime path relative to the run directory ----------------------------

def test_declared_runtime_path_relative_to_the_run_dir_is_found(tmp_path):
    out = tmp_path / "outputs" / "compose-substrate"
    out.mkdir(parents=True)
    runtime = _runtime(out)
    resolved = primitives._resolve_runtime_metadata_path(
        _Handle(tmp_path),
        {"runtime_metadata_path": "outputs/compose-substrate/runtime.json"},
        primitive="shim_peer_malformed_blockfetch",
    )
    assert Path(resolved) == runtime


# --- F75: freeze a containerised node through the container runtime -------------------------

def test_node_freeze_pauses_and_unpauses_the_container_when_the_node_is_containerised(tmp_path):
    commands = []
    state = {"paused": False}

    def executor(command, *, timeout=None, dry_run=False):
        commands.append(command)
        if command.startswith("docker pause "):
            state["paused"] = True
        elif command.startswith("docker unpause "):
            state["paused"] = False
        stdout = ("true\n" if state["paused"] else "false\n") if "inspect" in command else ""
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    prim = primitives.FaultNodeFreeze(params={"target_node": "node1", "executor": executor})
    node = {"id": "node1", "container_name": "dwarf-x-node1-1", "port": 3001}
    prim._runtime_node = lambda handle=None: (tmp_path / "runtime.json", "node1", node)
    handle = _Handle(tmp_path)
    prim.apply(handle)
    prim.remove(handle)
    assert any(c.startswith("docker pause dwarf-x-node1-1") for c in commands)
    assert any(c.startswith("docker unpause dwarf-x-node1-1") for c in commands)
    assert not any(c.startswith("ps") or "kill -STOP" in c or "kill -CONT" in c for c in commands)
    applied, removed = [e for e in handle.logs if e["event"] in ("applied", "removed")]
    assert applied["payload"]["container"] == "dwarf-x-node1-1" and applied["payload"]["state"] == "paused"
    assert removed["payload"]["state"] == "running"


def test_node_freeze_keeps_the_host_pid_path_for_host_nodes(tmp_path, monkeypatch):
    commands = []

    def executor(command, *, timeout=None, dry_run=False):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    prim = primitives.FaultNodeFreeze(params={"target_node": "node1", "executor": executor})
    prim._runtime_node = lambda handle=None: (tmp_path / "runtime.json", "node1", {"id": "node1", "port": 3001})
    monkeypatch.setattr(prim, "_resolve_target_pid", lambda handle=None: 4242)
    monkeypatch.setattr(prim, "_wait_for_stopped_state", lambda pid: "T")
    prim.apply(_Handle(tmp_path))
    assert commands == ["sudo kill -STOP 4242"]


# --- F76: host-model port faults say so on a container substrate ----------------------------

@pytest.mark.parametrize("cls", [primitives.FaultLocalPortDrop, primitives.FaultLocalPortDelay])
def test_local_port_faults_refuse_container_substrates_with_a_reason(cls, tmp_path):
    body = json.loads(_runtime(tmp_path).read_text(encoding="utf-8"))
    body["haskell_nodes"][0]["container_name"] = "dwarf-x-node1-1"
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps(body), encoding="utf-8")
    prim = cls(params={"target_node": "node1", "runtime_metadata_path": "~/cardano-profiles/profile-x/runtime.json"})
    with pytest.raises(ValueError, match="host-model fault"):
        prim._target_port(_Handle(tmp_path, prepared=runtime))


def test_failed_verification_cleanup_captures_container_logs_before_teardown(tmp_path):
    calls = []

    def runner(command, **_kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "compose", "--project-name"] and "logs" in command:
            return SimpleNamespace(returncode=0, stdout="node1-1  | boot\nnode2-1  | crash\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = compose._cleanup_failed_substrate(
        docker_project="dwarf-substrate-z", runtime_root=tmp_path, controller_attachment=None,
        runner=runner, output_dir=tmp_path / "out",
    )
    logs = tmp_path / "out" / "failed-substrate-logs.txt"
    assert logs.read_text(encoding="utf-8").splitlines()[-1] == "node2-1  | crash"
    assert report["logs_path"] == str(logs)
    down = next(i for i, c in enumerate(calls) if "down" in c)
    captured = next(i for i, c in enumerate(calls) if "logs" in c)
    assert captured < down


def test_startup_gate_failures_also_tear_the_substrate_down_with_logs():
    source = Path(compose.__file__).read_text(encoding="utf-8")
    docker_fn = source[source.index("def _compose_substrate_docker("):source.index("def _compose_substrate_docker_multihost(")]
    assert docker_fn.count("_cleanup_failed_substrate(") == 3  # verification + cardano gate + amaru gate
    assert docker_fn.count("output_dir=output_dir,") >= 3


# --- F83: public-network topologies keep the scenario's inter-node edges ---------------------

def _plan():
    nodes = [{"id": f"node{i}", "impl": "cardano-node", "version": "10.7.1", "role": "honest"} for i in (1, 2, 3)]
    edges = [{"from": a["id"], "to": b["id"]} for a in nodes for b in nodes if a is not b]
    return {"nodes": nodes, "topology": {"edges": edges}}


def test_sibling_container_access_points_follow_the_scenario_edges():
    plan = _plan()
    points = compose._sibling_container_access_points(plan["nodes"][1], plan)
    assert [p["address"] for p in points] == ["node1", "node3"]
    assert all(isinstance(p["port"], int) for p in points)


def test_public_branch_passes_sibling_endpoints_next_to_the_upstream_relay():
    source = Path(compose.__file__).read_text(encoding="utf-8")
    docker_fn = source[source.index("def _compose_substrate_docker("):source.index("def _compose_substrate_docker_multihost(")]
    assert "access_points=_sibling_container_access_points(node, plan) + upstream_access_points" in docker_fn
    # the custom-testnet branch uses the same helper
    assert docker_fn.count("_sibling_container_access_points(node, plan)") >= 2
