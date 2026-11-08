"""F95: port drop / delay faults on a containerised node act inside the node's network namespace.

The host-model faults shaped the controller's loopback, which never carries container traffic.
For a container node the fault runs a one-shot helper (the controller's own image, NET_ADMIN only)
in the node's namespace (``--network container:<node>``); iptables and tc rules persist in that
namespace until the matching remove step deletes them.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from profile_manager import primitives


class _Handle:
    def __init__(self, run_dir, prepared):
        self.run_dir = run_dir
        self.logs = []
        self._prepared_runtime_metadata_path = str(prepared)

    def log(self, **entry):
        self.logs.append(entry)


def _runtime(tmp_path):
    body = {"haskell_nodes": [{"id": "node1", "impl": "cardano-node", "port": 37971, "listen_address": "127.0.0.1:37971",
                               "container_name": "dwarf-x-node1-1", "container_ip": "10.9.0.2",
                               "container_listen_address": "node1:3001"}], "amaru_nodes": []}
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _executor(calls):
    def run(command, *, timeout=None, dry_run=False):
        calls.append(command)
        if command.startswith("docker inspect"):
            return SimpleNamespace(returncode=0, stdout="sha256:" + "c" * 64 + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def _helper_calls(calls):
    return [c for c in calls if c.startswith("docker run")]


def test_port_drop_on_a_container_node_applies_and_removes_rules_in_its_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "controllerid")
    calls = []
    prim = primitives.FaultLocalPortDrop(params={"target_node": "node1", "runtime_metadata_path": "outputs/x/runtime.json",
                                                 "executor": _executor(calls)})
    handle = _Handle(tmp_path, _runtime(tmp_path))
    prim.apply(handle)
    prim.remove(handle)
    runs = _helper_calls(calls)
    assert len(runs) == 2
    for c in runs:
        assert "--network container:dwarf-x-node1-1" in c and "--cap-add NET_ADMIN" in c and "sha256:" + "c" * 64 in c
        assert "sudo" not in c
    assert "iptables -w -I INPUT -p tcp --dport 3001 -j DROP" in runs[0]
    assert "iptables -w -I OUTPUT -p tcp --sport 3001 -j DROP" in runs[0]
    assert "iptables -w -D INPUT -p tcp --dport 3001 -j DROP" in runs[1]
    applied = [e for e in handle.logs if e["event"] == "applied"][0]["payload"]
    assert applied["mechanism"] == "container-netns" and applied["container"] == "dwarf-x-node1-1" and applied["port"] == 3001


def test_port_delay_on_a_container_node_shapes_its_default_interface(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "controllerid")
    calls = []
    prim = primitives.FaultLocalPortDelay(params={"target_node": "node1", "runtime_metadata_path": "outputs/x/runtime.json",
                                                  "delay_ms": 250, "jitter_ms": 20, "executor": _executor(calls)})
    handle = _Handle(tmp_path, _runtime(tmp_path))
    prim.apply(handle)
    prim.remove(handle)
    runs = _helper_calls(calls)
    assert len(runs) == 2
    assert "netem delay 250ms 20ms" in runs[0]
    assert "match ip sport 3001 0xffff" in runs[0] and "match ip dport 3001 0xffff" in runs[0]
    assert "ip -o route show default" in runs[0]
    assert "tc qdisc del dev" in runs[1]
    applied = [e for e in handle.logs if e["event"] == "applied"][0]["payload"]
    assert applied["mechanism"] == "container-netns" and applied["delay_ms"] == 250


def test_host_nodes_keep_the_loopback_model(tmp_path):
    body = {"haskell_nodes": [{"id": "node1", "impl": "cardano-node", "port": 3101}], "amaru_nodes": []}
    path = tmp_path / "runtime.json"; path.write_text(json.dumps(body), encoding="utf-8")
    calls = []
    prim = primitives.FaultLocalPortDrop(params={"target_node": "node1", "runtime_metadata_path": "outputs/x/runtime.json",
                                                 "executor": _executor(calls)})
    prim.apply(_Handle(tmp_path, path))
    assert any(c.startswith("sudo iptables -w -I OUTPUT") and "--dport 3101" in c for c in calls)
    assert not _helper_calls(calls)


class _Runtime:
    def __init__(self):
        self.marks = []

    def mark_phase(self, phase_id, state, phase_index=None):
        self.marks.append((phase_id, state))
        return {}


@pytest.mark.parametrize("cls,extra", [(primitives.FaultLocalPortDrop, {}), (primitives.FaultLocalPortDelay, {"delay_ms": 100})])
def test_container_port_faults_mark_the_hostile_window_for_the_taps(cls, extra, tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "controllerid")
    calls = []
    prim = cls(params={"target_node": "node1", "runtime_metadata_path": "outputs/x/runtime.json", "executor": _executor(calls), **extra})
    handle = _Handle(tmp_path, _runtime(tmp_path)); handle._measurement_runtime = _Runtime()
    prim.apply(handle); prim.remove(handle)
    assert handle._measurement_runtime.marks == [("hostile", "start"), ("hostile", "end")]


def test_node_freeze_marks_the_hostile_window_for_the_taps(tmp_path):
    from types import SimpleNamespace as NS
    state = {"paused": False}

    def executor(command, *, timeout=None, dry_run=False):
        if command.startswith("docker pause"): state["paused"] = True
        if command.startswith("docker unpause"): state["paused"] = False
        return NS(returncode=0, stdout=("true\n" if state["paused"] else "false\n") if "inspect" in command else "", stderr="")

    prim = primitives.FaultNodeFreeze(params={"target_node": "node1", "executor": executor})
    prim._runtime_node = lambda handle=None: (tmp_path / "runtime.json", "node1", {"id": "node1", "container_name": "c1", "port": 3001})
    handle = _Handle(tmp_path, tmp_path / "runtime.json"); handle._measurement_runtime = _Runtime()
    prim.apply(handle); prim.remove(handle)
    assert handle._measurement_runtime.marks == [("hostile", "start"), ("hostile", "end")]
