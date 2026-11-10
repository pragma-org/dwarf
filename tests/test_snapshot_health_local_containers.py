"""F120: snapshot/checkpoint health of a single-host container node is probed locally.

Live d5eb22eb: node1 was restarted and running, yet node_healthy_after_restart was False. The node
record has host_id "local", which sent it down the multi-host branch (docker over ssh to a host
named "local"); a legacy tmux session name would then have been required; and the container IP
from runtime metadata can change after docker start.
"""
from __future__ import annotations

from types import SimpleNamespace

from scripts import runtime_snapshot_substrate as subject


def _node():
    return {"id": "node1", "impl": "cardano-node", "host_id": "local", "host_ssh_target": None,
            "container_name": "dwarf-s-node1-1", "container_ip": "192.0.2.1.3", "container_listen_address": "node1:3001",
            "listen_address": "127.0.0.1:34961", "session": "dwarf-s-node1", "health_probe": "path-only"}


def test_local_container_node_is_probed_at_its_current_address(monkeypatch):
    probed = {}
    monkeypatch.setattr(subject, "_wait_multihost_docker_node_healthy",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ssh for a local node")))
    monkeypatch.setattr(subject, "_tmux_session_exists", lambda name: (_ for _ in ()).throw(AssertionError("no tmux")))
    monkeypatch.setattr(subject, "run_command", lambda argv, **k: SimpleNamespace(returncode=0, stdout="192.0.2.1.9\n", stderr=""))

    def healthy(nodes, *, timeout_seconds):
        probed["node"] = nodes[0]
        return [{"healthy": True, "probed_endpoint": f"{nodes[0]['container_ip']}:3001"}]

    monkeypatch.setattr(subject, "wait_for_nodes_healthy", healthy)
    status = subject._node_health_status(_node(), timeout_seconds=5)
    assert status["healthy"] is True and status["probe"] == "local-container"
    assert probed["node"]["container_ip"] == "192.0.2.1.9" and status["probed_endpoint"] == "192.0.2.1.9:3001"


def test_a_real_remote_host_keeps_the_multihost_probe(monkeypatch):
    node = {**_node(), "host_id": "box-2", "host_ssh_target": "dwarf@box-2"}
    monkeypatch.setattr(subject, "_wait_multihost_docker_node_healthy", lambda *a, **k: True)
    assert subject._node_health_status(node, timeout_seconds=5)["probe"] == "multihost-docker"
