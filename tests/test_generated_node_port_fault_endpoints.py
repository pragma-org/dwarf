"""F106: the generated-node port-drop check probes container nodes at their container address.

It probed 127.0.0.1:<published port>, which the controller can never reach: the healthy node read
as unreachable, and the blocked-node check passed without testing anything.
"""
from __future__ import annotations

import json

import pytest

from scripts import runtime_generated_node_port_fault_check as check


def _runtime(tmp_path, *, container=True):
    nodes = []
    for index, name in enumerate(("node1", "node2"), 1):
        entry = {"id": name, "port": 34300 + index}
        if container:
            entry.update({"container_ip": f"10.9.0.{index + 1}", "container_listen_address": f"{name}:3001"})
        nodes.append(entry)
    (tmp_path / "runtime.json").write_text(json.dumps({"haskell_nodes": nodes}), encoding="utf-8")
    return tmp_path


def test_container_nodes_are_probed_at_their_container_address(tmp_path, monkeypatch):
    probed = []
    monkeypatch.setattr(check, "_listener_ok", lambda host, port: probed.append((host, port)) or host == "10.9.0.2")
    assert check.run_node_port_drop_check(runtime_root=_runtime(tmp_path), blocked_node="node2", healthy_nodes=["node1"]) == 0
    assert probed == [("10.9.0.3", 3001), ("10.9.0.2", 3001)]


def test_host_nodes_keep_loopback(tmp_path, monkeypatch):
    probed = []
    monkeypatch.setattr(check, "_listener_ok", lambda host, port: probed.append((host, port)) or port == 34301)
    check.run_node_port_drop_check(runtime_root=_runtime(tmp_path, container=False), blocked_node="node2", healthy_nodes=["node1"])
    assert probed == [("127.0.0.1", 34302), ("127.0.0.1", 34301)]
