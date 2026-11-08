"""F81: controller runtime records identify nodes by ``id``; legacy helpers looked up ``name``.

Both directions are covered: the helper scripts accept ``id`` where they read ``name``, and
the compose writer stamps ``name`` (= ``id``) on every record so user-authored helpers that
still read ``name`` keep working.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import runtime_generated_node_freeze_check as freeze_check
from scripts import runtime_generated_node_port_fault_check as port_fault_check
from scripts import runtime_haskell_gc_capture as gc_capture
from scripts import runtime_substrate_common as common
from scripts import runtime_syscall_trace as syscall_trace


def _runtime(tmp_path):
    body = {"haskell_nodes": [{"id": "node1", "impl": "cardano-node", "port": 3101, "listen_address": "127.0.0.1:3101",
                               "container_name": "dwarf-x-node1-1", "log_path": str(tmp_path / "node1.log")}],
            "amaru_nodes": []}
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


@pytest.mark.parametrize("module", [freeze_check, port_fault_check])
def test_generated_node_checks_index_nodes_by_id_when_name_is_absent(module, tmp_path):
    _runtime(tmp_path)
    nodes = module._runtime_json_nodes(tmp_path)
    assert "node1" in nodes and nodes["node1"]["port"] == 3101


@pytest.mark.parametrize("module", [gc_capture, syscall_trace])
def test_pid_helpers_find_the_target_node_by_id(module, tmp_path):
    path = _runtime(tmp_path)
    node = module._load_runtime_node(path, "node1")
    assert node["id"] == "node1"


def test_legacy_name_alias_is_stamped_on_runtime_records():
    record = {"id": "node2", "impl": "amaru"}
    assert common.with_legacy_node_name(record) == {"id": "node2", "impl": "amaru", "name": "node2"}
    assert common.with_legacy_node_name({"id": "n", "name": "custom"})["name"] == "custom"


def test_compose_writer_stamps_the_alias_on_every_metadata_record():
    source = Path("dwarf/scripts/runtime_compose_substrate.py").read_text(encoding="utf-8")
    assert source.count("with_legacy_node_name(") >= 2  # single-host and multihost writers


# --- F85: the generated-node checks resolve a container node's pid like the shared resolver ---

def test_freeze_check_resolves_a_container_node_pid_through_the_shared_docker_resolver(monkeypatch):
    monkeypatch.setattr(freeze_check, "_resolve_docker_pid", lambda node: 4242)
    monkeypatch.setattr(freeze_check, "_ps", lambda *a: (_ for _ in ()).throw(AssertionError("ps must not be scanned")))
    node = {"id": "node1", "container_name": "dwarf-x-node1-1", "port": 37971, "container_listen_address": "node1:3001"}
    assert freeze_check._resolve_node_pid(node) == 4242


def test_freeze_check_falls_back_to_the_container_port_in_the_process_table(monkeypatch):
    monkeypatch.setattr(freeze_check, "_resolve_docker_pid", lambda node: None)

    class _R:
        returncode = 0
        stdout = "  5151 cardano-node cardano-node run --port 3001 --config /env/configuration.yaml\n  6161 cardano-node cardano-node run --port 3002\n"

    monkeypatch.setattr(freeze_check, "_ps", lambda *a: _R())
    node = {"id": "node1", "container_name": "dwarf-x-node1-1", "port": 37971, "container_listen_address": "node1:3001"}
    assert freeze_check._resolve_node_pid(node) == 5151
