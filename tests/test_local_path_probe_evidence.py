"""Local-path wiring campaign, third layer (2026-11-08): F62 and F63.

F62: the window scenario's two probes (``runtime_target_health_and_progress``,
``runtime_peer_session_health``) read the ``target_health`` and ``peer_session``
sections that ``runtime_protocol_decode_cases`` writes; the txsubmission probe
family never produced them, so the window scenario could not evaluate three of
its four assertions on the local path.  With ``evidence_capture: true`` in
prepared-runtime mode the probe family records the same sections around its
probe, reusing the observers the probes themselves re-observe with.

F63: in substrate-runtime mode the probe script connected to
``127.0.0.1:<published port>``; a controller-run self-composed devnet is only
reachable at the node's container address, as the F55 shim fix already does.
"""
import json
import socket
import struct
import subprocess
import threading

import pytest

from profile_manager import primitives
from scripts import runtime_txsubmission_probe as probe


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.logs = []

    def log(self, **entry):
        self.logs.append(entry)


def _prepared_target(handle, params):
    return {"id": "amaru-relay-1", "implementation": "amaru", "container": "dwarf-profile-z-amaru-relay-1-1",
            "listen_host": "10.192.7.10", "listen_port": 3000, "network_magic": 42,
            "runtime_metadata_path": str(handle.run_dir / "runtime.json")}


def _fake_probe_run(monkeypatch, tmp_path):
    monkeypatch.setattr(primitives, "_resolve_client_runtime_target", _prepared_target)
    monkeypatch.setattr(primitives, "build_runtime_txsubmission_probe_command", lambda *, config_path, mode: ["true"])

    def fake_run(command, **kwargs):
        (tmp_path / "outputs" / "txsubmission-window").mkdir(parents=True, exist_ok=True)
        (tmp_path / "outputs" / "txsubmission-window" / "result.json").write_text(
            json.dumps({"mode": "txsubmission_window_pressure", "handshake_response_kind": "accepted"}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(primitives.subprocess, "run", fake_run)
    (tmp_path / "runtime.json").write_text(json.dumps({
        "network_magic": 42,
        "actual_topology": {"isolated_consumer": "amaru-consumer"},
        "identity": {"services": {"amaru-consumer": {"container": "dwarf-profile-z-amaru-consumer-1"}}},
    }), encoding="utf-8")
    observations = []
    monkeypatch.setattr(primitives, "_protocol_container_state", lambda container: {"running": True, "status": "running"})
    monkeypatch.setattr(primitives, "_observe_protocol_target", lambda observer: observations.append(("target", observer)) or {
        "state": {"running": True, "status": "running"}, "tip": {"block_height": 7, "hash": "ab", "slot": 70}, "log_signals": {}})
    monkeypatch.setattr(primitives, "_observe_protocol_peer", lambda observer: observations.append(("peer", observer)) or {
        "observed_at_epoch_seconds": 1.0, "peer_id": observer["peer_id"], "container": observer["container"], "usable": True,
        "tip": {"block_height": 7, "hash": "ab", "slot": 70}})
    return observations


def test_window_pressure_records_target_health_and_peer_session_for_the_probes(tmp_path, monkeypatch):
    observations = _fake_probe_run(monkeypatch, tmp_path)
    prim = primitives.RuntimeTxsubmissionWindowPressure(params={
        "profile_id": "profile-z-amaru-20260918-nanoseconds-v3", "output_dir": "outputs/txsubmission-window",
        "evidence_capture": True,
    })
    prim.run(_Handle(tmp_path), None)
    report = json.loads((tmp_path / "outputs" / "txsubmission-window" / "result.json").read_text(encoding="utf-8"))
    assert report["handshake_response_kind"] == "accepted"            # the probe's own result is untouched
    health = report["target_health"]
    assert health["observer"] == {"implementation": "amaru", "container": "dwarf-profile-z-amaru-relay-1-1",
                                  "started_at": health["observer"]["started_at"]}
    assert health["before"]["running"] is True and health["tip_before"]["block_height"] == 7
    assert health["after"]["running"] is True and health["tip_after"]["block_height"] == 7
    peer = report["peer_session"]
    assert peer["peer_id"] == "amaru-consumer"
    assert peer["observer"] == {"peer_id": "amaru-consumer", "container": "dwarf-profile-z-amaru-consumer-1",
                                "network_magic": 42, "socket_path": "/state/node.socket"}
    assert all(peer[phase]["usable"] is True for phase in ("before", "during", "after"))
    assert peer["recovered_within_seconds"] == 0.0
    # The probes can now read the window result through report_path.
    probe_primitive = primitives.RuntimePeerSessionHealth(params={"report_path": "outputs/txsubmission-window/result.json"})
    assert primitives._protocol_decode_report(_Handle(tmp_path), probe_primitive.params)["peer_session"]["peer_id"] == "amaru-consumer"
    # Observation order: target+peer before the probe, then during/after.
    assert [kind for kind, _ in observations][:2] == ["target", "peer"]


def test_window_pressure_without_evidence_capture_leaves_the_result_alone(tmp_path, monkeypatch):
    observations = _fake_probe_run(monkeypatch, tmp_path)
    prim = primitives.RuntimeTxsubmissionWindowPressure(params={
        "profile_id": "profile-z-amaru-20260918-nanoseconds-v3", "output_dir": "outputs/txsubmission-window",
    })
    prim.run(_Handle(tmp_path), None)
    report = json.loads((tmp_path / "outputs" / "txsubmission-window" / "result.json").read_text(encoding="utf-8"))
    assert "target_health" not in report and "peer_session" not in report
    assert observations == []


# --- F63: the probe script addresses a container-run node by its container endpoint


class _WindowNode(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.server = socket.socket(); self.server.bind(("127.0.0.1", 0)); self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.received = []

    def run(self):
        try:
            conn, _ = self.server.accept(); conn.settimeout(5)
            for _ in range(3):
                header = probe._recv_exact(conn, 8); _ts, word = struct.unpack(">II", header)
                self.received.append(probe._recv_exact(conn, word & 0xFFFF))
                if len(self.received) == 1:
                    conn.sendall(probe._encode_mux_sdu(bytes.fromhex("83010e84182af400f4"), mini_protocol_num=0, initiator=False))
                elif len(self.received) == 2:
                    conn.sendall(probe._encode_mux_sdu(bytes([0x84, 0x00, 0xF5, 0x00, 0x03]), mini_protocol_num=4, initiator=False))
            conn.close()
        finally:
            self.server.close()


def test_substrate_runtime_mode_uses_the_container_endpoint_over_the_published_port(tmp_path):
    node = _WindowNode(); node.start()
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"network_magic": 42, "haskell_nodes": [{
        "id": "node1", "impl": "cardano-node", "port": 1, "listen_address": "127.0.0.1:1",
        "container_ip": "127.0.0.1", "container_listen_address": f"node1:{node.port}",
    }]}), encoding="utf-8")
    report = probe.run_probe(runtime_metadata_path=runtime, target_node="node1", output_dir=tmp_path / "w",
                             mode="txsubmission_window_pressure", response_timeout_seconds=2.0)
    node.join(5)
    assert report["handshake_response_kind"] == "accepted"
    assert report["target_host"] == "127.0.0.1" and report["target_port"] == node.port


def test_substrate_runtime_mode_keeps_the_published_port_for_host_era_records(tmp_path):
    node = _WindowNode(); node.start()
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"network_magic": 42, "haskell_nodes": [{"name": "node1", "port": node.port}]}), encoding="utf-8")
    report = probe.run_probe(runtime_metadata_path=runtime, target_node="node1", output_dir=tmp_path / "w",
                             mode="txsubmission_window_pressure", response_timeout_seconds=2.0)
    node.join(5)
    assert report["handshake_response_kind"] == "accepted" and report["target_port"] == node.port
