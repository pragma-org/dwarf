"""F128: resource-abuse faults record only real evidence and route their proxy on docker substrates.

Every mode merged _default_observation_overrides into the observation: fake tips
("resource-abuse-tip-0", slot 0), topology-copied peer edges and a single fake tip group. Those
reached observation-summary.json, so slow-loris, disk-full and threshold-delay passed vacuously and
the bandwidth throttle failed substrate_quorum_observed with real_tip_count 0. The bandwidth proxy
was also advertised at 127.0.0.1 (the node's own container) with the upstream's host-published
address, and its throughput was hard-coded to 0.
"""
from __future__ import annotations

import json

from scripts import runtime_resource_abuse_fault as fault

META = {
    "compose_mode": "docker", "compose_project": "dwarf-s", "runtime_root": "/tmp/rr",
    "topology": {"edges": [{"from": "node1", "to": "node2"}]},
    "nodes": [
        {"id": "node1", "container_name": "dwarf-s-node1-1", "container_network": "dwarf-s-net",
         "listen_address": "127.0.0.1:34021", "container_listen_address": "node1:3001", "topology_path": "/tmp/t1.json"},
        {"id": "node2", "container_name": "dwarf-s-node2-1", "container_network": "dwarf-s-net",
         "listen_address": "127.0.0.1:34022", "container_listen_address": "node2:3001", "topology_path": "/tmp/t2.json"},
    ],
}


def test_docker_route_uses_the_controller_address_and_the_upstream_container(monkeypatch):
    monkeypatch.setattr(fault, "_proxy_bind_address", lambda network: "10.9.0.99" if network == "dwarf-s-net" else "?")
    assert fault._proxy_route(META, META["nodes"][0], META["nodes"][1]) == ("10.9.0.99", "node2:3001")


def test_host_route_is_unchanged():
    host = {**META, "compose_mode": "host"}
    node = {**META["nodes"][0], "host_published_address": None}
    assert fault._proxy_route(host, node, META["nodes"][1]) == ("127.0.0.1", "127.0.0.1:34022")


def test_no_fake_observation_is_written():
    assert not hasattr(fault, "_default_observation_overrides")


def test_bandwidth_throttle_reports_measured_throughput(tmp_path, monkeypatch):
    stats_path = tmp_path / "stats.json"
    stats_path.write_text(json.dumps({"elapsed_seconds": 4.0, "bytes_forwarded": 40000, "connections_seen": 1}))
    written = {}
    rewrites = []
    launches = []
    monkeypatch.setattr(fault, "_read_metadata", lambda p: json.loads(json.dumps(META)))
    monkeypatch.setattr(fault, "_write_metadata", lambda p, m: written.update(m))
    monkeypatch.setattr(fault, "_pick_free_port", lambda: 40000)
    monkeypatch.setattr(fault, "_proxy_bind_address", lambda network: "10.9.0.99")
    monkeypatch.setattr(fault, "_launch_proxy_session", lambda **k: launches.append(k) or {"stats_path": str(stats_path)})
    monkeypatch.setattr(fault, "_rewrite_topology_path", lambda **k: rewrites.append(k))
    monkeypatch.setattr(fault, "_restart_node", lambda node, compose_mode: None)
    monkeypatch.setattr(fault, "wait_for_nodes_healthy", lambda nodes, timeout_seconds: [{"healthy": True}])
    report = fault.apply_bandwidth_throttle(runtime_metadata_path=tmp_path / "runtime.json", output_dir=tmp_path / "out",
                                            from_node="node1", to_node="node2", kilobits_per_second=128,
                                            healthy_timeout_seconds=5)
    assert launches[0]["upstream_address"] == "node2:3001"
    assert (rewrites[0]["proxy_host"], rewrites[0]["proxy_port"]) == ("10.9.0.99", 40000)
    assert report["result"]["observed_throughput_bytes_per_sec"] == 10000
    assert report["result"]["throttle_applied"] is True
    assert "resource-abuse-tip-0" not in json.dumps(written)
