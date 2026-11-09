"""Local-path wiring (2026-11-08 campaign, defects B and D).

Runtime-aware primitives must find the deployed topology the way the run
service bound it (the prepared runtime, then the controller's retained
runtime for the run's profile) instead of a literal host-era path, address a
node by the container endpoint the controller can reach while attached to the
topology network, and accept the controller's ``id``-keyed node records.

A self-composed mixed devnet must pin ``ExperimentalProtocolsEnabled`` for
cardano-node 11.1+ exactly as profile deploys and Antithesis rigs already do.
"""
import json
import socket
import struct
import threading
from pathlib import Path

import pytest

from profile_manager import primitives
from scripts import runtime_compose_substrate as compose
from scripts import runtime_substrate_common as common


def _runtime_body(port, *, container=True):
    node = {"id": "node1", "impl": "cardano-node", "port": 1, "listen_address": "127.0.0.1:1"}
    if container:
        node.update({
            "container_ip": "127.0.0.1",
            "container_listen_address": f"node1:{port}",
            "container_name": "dwarf-profile-x-node1-1",
            "container_network": "dwarf-profile-x-net",
        })
    return {"profile_id": "profile-x", "haskell_nodes": [node], "amaru_nodes": []}


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


class _Handle:
    def __init__(self, run_dir, *, prepared=None, profile_id=None):
        self.run_dir = run_dir
        self.logs = []
        self._profile_id = profile_id
        if prepared is not None:
            self._prepared_runtime_metadata_path = str(prepared)

    def log(self, **entry):
        self.logs.append(entry)


class _Node(threading.Thread):
    """One accept + one reply + one more frame, like the F53 test node."""

    def __init__(self, reply_hex):
        super().__init__(daemon=True)
        self.server = socket.socket(); self.server.bind(("127.0.0.1", 0)); self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.reply = bytes.fromhex(reply_hex)
        self.payloads = []

    def run(self):
        try:
            conn, _ = self.server.accept(); conn.settimeout(5)
            header = conn.recv(8); _ts, word = struct.unpack(">II", header)
            self.payloads.append(conn.recv(word & 0xFFFF))
            conn.sendall(struct.pack(">II", 0, (1 << 31) | len(self.reply)) + self.reply)
            try:
                header = conn.recv(8)
                if len(header) == 8:
                    _ts, word = struct.unpack(">II", header)
                    self.payloads.append(conn.recv(word & 0xFFFF))
            except OSError:
                pass
            conn.close()
        finally:
            self.server.close()


LEGACY = "~/cardano-profiles/profile-x/runtime.json"


def test_prepared_runtime_wins_over_the_declared_legacy_path(tmp_path):
    prepared = _write(tmp_path / "current" / "runtime.json", _runtime_body(3001))
    handle = _Handle(tmp_path, prepared=prepared)
    resolved = primitives._resolve_runtime_metadata_path(
        handle, {"runtime_metadata_path": LEGACY, "target_node": "node1"}, primitive="shim_peer_raw_bytes"
    )
    assert resolved == prepared


def test_declared_path_is_used_when_it_exists_and_nothing_was_prepared(tmp_path):
    declared = _write(tmp_path / "legacy" / "runtime.json", _runtime_body(3001, container=False))
    resolved = primitives._resolve_runtime_metadata_path(
        _Handle(tmp_path), {"runtime_metadata_path": str(declared)}, primitive="fault_node_freeze"
    )
    assert resolved == declared


def test_controller_profile_runtime_is_the_last_resort(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(tmp_path / "topology-runtimes"))
    retained = _write(
        tmp_path / "topology-runtimes" / "profile-x" / "current" / "runtime.json", _runtime_body(3001)
    )
    resolved = primitives._resolve_runtime_metadata_path(
        _Handle(tmp_path, profile_id="profile-x"), {"runtime_metadata_path": LEGACY}, primitive="shim_peer_raw_bytes"
    )
    assert resolved == retained


def test_missing_runtime_names_every_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_REMOTE_BASE", str(tmp_path / "nowhere"))
    with pytest.raises(ValueError, match="shim_peer_raw_bytes runtime metadata missing") as excinfo:
        primitives._resolve_runtime_metadata_path(
            _Handle(tmp_path, profile_id="profile-x"), {"runtime_metadata_path": LEGACY}, primitive="shim_peer_raw_bytes"
        )
    message = str(excinfo.value)
    # the declared legacy path is named as resolved onto the installed layout (F71)
    assert str(tmp_path / "nowhere" / "profile-x" / "runtime.json") in message
    assert str(tmp_path / "nowhere" / "profile-x") in message


def test_node_lookup_accepts_controller_id_records_and_legacy_names():
    assert primitives._runtime_metadata_node({"haskell_nodes": [{"id": "node1"}]}, "node1") == {"id": "node1"}
    assert primitives._runtime_metadata_node({"haskell_nodes": [{"name": "node1"}]}, "node1") == {"name": "node1"}
    assert primitives._runtime_metadata_node(
        {"haskell_nodes": [], "amaru_nodes": [{"id": "amaru1"}]}, "amaru1"
    ) == {"id": "amaru1"}
    assert primitives._runtime_metadata_node({"haskell_nodes": [{"id": "node1"}]}, "node9") is None


def test_endpoint_prefers_the_container_address_over_the_host_loopback_port():
    node = {"port": 1, "container_ip": "10.192.8.2", "container_listen_address": "node1:3001"}
    assert primitives._runtime_node_endpoint(node, {}) == ("10.192.8.2", 3001)
    # The catalog's "127.0.0.1" is the host-era default, not a routing decision.
    assert primitives._runtime_node_endpoint(node, {"target_host": "127.0.0.1"}) == ("10.192.8.2", 3001)
    assert primitives._runtime_node_endpoint(node, {"target_host": "192.0.2.16"}) == ("192.0.2.16", 1)
    assert primitives._runtime_node_endpoint({"port": 34301}, {}) == ("127.0.0.1", 34301)


def test_shim_peer_connects_to_the_container_endpoint_of_the_prepared_runtime(tmp_path):
    node = _Node("83010e84182af400f4")   # MsgAccept 14
    node.start()
    prepared = _write(tmp_path / "runtime.json", _runtime_body(node.port))
    handle = _Handle(tmp_path, prepared=prepared)
    prim = primitives.ShimPeerMalformedTxsubmission(params={
        "malformation_id": "bad-requesttxids-shape", "response_timeout_seconds": 2,
        "runtime_metadata_path": LEGACY, "target_node": "node1",
    })
    prim.run(handle, None)
    node.join(5)
    assert len(node.payloads) == 2
    started = next(entry for entry in handle.logs if entry.get("event") == "started")
    assert started["payload"]["runtime_metadata_path"] == str(prepared)
    assert started["payload"]["target_host"] == "127.0.0.1"
    assert started["payload"]["target_port"] == node.port


@pytest.mark.parametrize("cls", [
    primitives.ShimPeerRawBytes, primitives.ShimPeerMalformedHandshake, primitives.ShimPeerMalformedBlockfetch,
    primitives.ShimPeerMalformedTxsubmission, primitives.ShimPeerInvalidCbor, primitives.FaultNodeFreeze,
])
def test_runtime_node_resolves_the_prepared_runtime_for_every_shim_and_freeze_primitive(tmp_path, cls):
    prepared = _write(tmp_path / "runtime.json", _runtime_body(3001))
    handle = _Handle(tmp_path, prepared=prepared)
    prim = cls(params={"runtime_metadata_path": LEGACY, "target_node": "node1"})
    path, target_node, node = prim._runtime_node(handle)
    assert (path, target_node, node["id"]) == (prepared, "node1", "node1")


@pytest.mark.parametrize("cls", [primitives.FaultLocalPortDrop, primitives.FaultLocalPortDelay])
def test_local_port_faults_resolve_the_prepared_runtime(tmp_path, cls):
    # host node record: the published loopback port is what iptables/tc act on
    prepared = _write(tmp_path / "runtime.json", _runtime_body(3001, container=False))
    prim = cls(params={"runtime_metadata_path": LEGACY, "target_node": "node1"})
    assert prim._target_port(_Handle(tmp_path, prepared=prepared)) == 1



# --- defect D: self-composed mixed devnets and the Amaru handshake -----------


def _mixed_nodes(cardano_version="11.1.2"):
    return [
        {"id": "node1", "impl": "cardano-node", "version": cardano_version, "role": "honest"},
        {"id": "node2", "impl": "amaru", "version": "10.11.20260918", "role": "honest"},
    ]


def test_mixed_devnet_pins_experimental_protocols_false_for_cardano_11_1(tmp_path):
    config_path = tmp_path / "configuration.yaml"
    config_path.write_text(json.dumps({"Protocol": "Cardano"}) + "\n", encoding="utf-8")
    result = compose._pin_cardano_experimental_protocols(config_path=config_path, nodes=_mixed_nodes())
    assert json.loads(config_path.read_text(encoding="utf-8"))["ExperimentalProtocolsEnabled"] is False
    assert result == {
        "pinned": True, "value": False, "policy_source": "amaru-interop-policy", "cardano_versions": ["11.1.2"],
    }


@pytest.mark.parametrize("nodes", [
    [{"id": "node1", "impl": "cardano-node", "version": "11.1.2", "role": "honest"}],   # cardano-only
    _mixed_nodes("10.7.1"),                                                              # pre-11.1
])
def test_cardano_only_and_pre_11_1_devnets_are_left_untouched(tmp_path, nodes):
    config_path = tmp_path / "configuration.yaml"
    config_path.write_text(json.dumps({"Protocol": "Cardano"}) + "\n", encoding="utf-8")
    result = compose._pin_cardano_experimental_protocols(config_path=config_path, nodes=nodes)
    assert "ExperimentalProtocolsEnabled" not in json.loads(config_path.read_text(encoding="utf-8"))
    assert result["pinned"] is False and result["value"] is None


def test_explicit_substrate_override_wins_over_the_policy(tmp_path):
    config_path = tmp_path / "configuration.yaml"
    config_path.write_text(json.dumps({"Protocol": "Cardano"}) + "\n", encoding="utf-8")
    result = compose._pin_cardano_experimental_protocols(
        config_path=config_path, nodes=_mixed_nodes(), explicit=True
    )
    assert json.loads(config_path.read_text(encoding="utf-8"))["ExperimentalProtocolsEnabled"] is True
    assert result["policy_source"] == "explicit" and result["value"] is True


def test_normalize_substrate_carries_the_experimental_protocols_override():
    substrate = {
        "compose_mode": "docker", "network": "testnet_42", "nodes": _mixed_nodes(),
        "topology": {"edges": [{"from": "node1", "to": "node2"}, {"from": "node2", "to": "node1"}]},
    }
    assert common.normalize_substrate(substrate)["cardano_experimental_protocols"] is None
    assert common.normalize_substrate({**substrate, "cardano_experimental_protocols": True})[
        "cardano_experimental_protocols"
    ] is True
    with pytest.raises(ValueError, match="cardano_experimental_protocols must be a boolean"):
        common.normalize_substrate({**substrate, "cardano_experimental_protocols": "yes"})
