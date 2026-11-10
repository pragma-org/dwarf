"""F126: wire probes run in the controller and connect to the node's container address.

runtime_exposure_probe (duplex promotion, mux ingress overrun, local-submit stress, handshake
version pressure) and runtime_hardening_probe (parser bounds, panic path) connected to
127.0.0.1:<published host port>, which inside the controller is not the node ("target N2N ingress
unreachable at 127.0.0.1:34021"; parser bounds "connect-failed"). They now share the tx-submission
probe's F63 rule through runtime_common.node_wire_endpoint.
"""
from __future__ import annotations

import json

import pytest

from scripts import runtime_common as common

NODE = {"id": "node1", "port": 34021, "container_ip": "10.9.0.2", "container_listen_address": "node1:3001"}


@pytest.mark.parametrize("target_host,expected", [
    (None, ("10.9.0.2", 3001)),
    ("127.0.0.1", ("10.9.0.2", 3001)),
    ("192.0.2.7", ("192.0.2.7", 34021)),
])
def test_endpoint_rule(target_host, expected):
    assert common.node_wire_endpoint(NODE, target_host) == expected


def test_host_era_node_keeps_the_published_port():
    assert common.node_wire_endpoint({"id": "n", "port": 3001}, None) == ("127.0.0.1", 3001)


PACKAGE_NODE = {"id": "node1", "port": 3001, "listen_address": "node1.example:3001",
                "container_listen_address": "node1.example:3001", "observation_transport": "antithesis-package"}


@pytest.mark.parametrize("target_host", [None, "127.0.0.1", "localhost"])
def test_package_node_uses_its_service_hostname(target_host):
    """Inside an Antithesis package there is no container_ip; the node is its compose hostname, not loopback."""
    assert common.node_wire_endpoint(PACKAGE_NODE, target_host) == ("node1.example", 3001)


def test_package_node_keeps_an_explicit_remote_host():
    assert common.node_wire_endpoint(PACKAGE_NODE, "192.0.2.7") == ("192.0.2.7", 3001)


def test_metadata_lookup(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"nodes": [NODE]}))
    assert common.runtime_node_wire_endpoint(path, "node1", "127.0.0.1") == ("10.9.0.2", 3001)


def test_hardening_probe_connects_to_the_container(tmp_path, monkeypatch):
    from scripts import runtime_hardening_probe as hardening
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"nodes": [NODE]}))
    seen = []

    def connect(address, timeout=None):
        seen.append(address)
        raise ConnectionRefusedError()

    monkeypatch.setattr(hardening.socket, "create_connection", connect)
    monkeypatch.setattr(hardening, "resolve_target_process", lambda p, n: {"port": 34021})
    hardening._send_malformed_txsubmission_sequence(runtime_metadata_path=path, target_node="node1", payload_hex="8100")
    assert seen and seen[0] == ("10.9.0.2", 3001)


@pytest.mark.parametrize("fn", ["_resolve_n2n_target"])
def test_exposure_n2n_target_uses_the_container(tmp_path, monkeypatch, fn):
    from scripts import runtime_exposure_probe as exposure
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"nodes": [NODE]}))
    monkeypatch.setattr(exposure, "resolve_target_process", lambda p, n: {"port": 34021, "pid": None})
    target = exposure._resolve_n2n_target(metadata={"nodes": [NODE]},
                                          config={"runtime_metadata_path": str(path), "target_node": "node1"})
    assert (target["host"], target["port"]) == ("10.9.0.2", 3001)
