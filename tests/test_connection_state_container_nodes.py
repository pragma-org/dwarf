"""F112: connection state of a container node is read inside the node with ss, not lsof/host ss.

runtime_connection_state.py ran ss and lsof in the controller against a host pid; the controller
has no lsof and its ss sees only its own namespace (live: e57722f2). Container nodes now reuse the
multi-node observation's container method (docker exec ss -tan in the node).
"""
from __future__ import annotations

import json
import subprocess

from scripts import runtime_connection_state as subject
from scripts import runtime_multi_node_observation as multi


def test_container_node_snapshot_reuses_the_in_container_ss(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"haskell_nodes": [{"name": "node1", "id": "node1", "port": 34301,
                                                      "container_name": "dwarf-p-node1-1",
                                                      "container_listen_address": "node1:3001"}]}))
    seen = {}

    def observe(**kwargs):
        seen.update(kwargs)
        return {"target_node": "node1", "pid": 4242, "port": 34301, "container_port": 3001,
                "connect_successes": 2, "connect_failures": 0,
                "ss_lines": ["LISTEN 0 4096 0.0.0.0:3001 0.0.0.0:*", "ESTAB 0 0 10.9.0.2:3001 10.9.0.3:40112"],
                "socket_observation": {"status": "available", "source": "ss"}}

    def no_host_tools(argv, *args, **kwargs):
        raise AssertionError(f"host tool called: {argv}")

    monkeypatch.setattr(multi, "_observe_connection_state", observe)
    monkeypatch.setattr(subprocess, "run", no_host_tools)
    summary = subject.run_snapshot(runtime_metadata_path=runtime, target_node="node1", output_dir=tmp_path / "out",
                                   snapshot_name="before", target_host="127.0.0.1", connect_attempts=2)
    assert seen["node"]["container_name"] == "dwarf-p-node1-1" and seen["connect_attempts"] == 2
    assert summary["ss_listen_count"] == 1 and summary["ss_established_count"] == 1
    assert summary["mechanism"] == "container-exec-ss" and summary["pid"] == 4242
    assert summary["connect_successes"] == 2 and summary["lsof_socket_count"] == 0
    assert "ESTAB" in (tmp_path / "out" / "ss.txt").read_text()
