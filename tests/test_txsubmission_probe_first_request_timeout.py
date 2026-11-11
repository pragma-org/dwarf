"""F124: the probe waits for cardano-node's first MsgRequestTxIds for the node's own init delay.

ouroboros-network's tx-submission server (Inbound V1 and V2) sleeps ``TxSubmissionInitDelay`` when it
starts on EACH connection, before its first MsgRequestTxIds. F119 waited for node startup instead,
so the probe still gave up after 2 s ("did not produce a data response; got timeout"). The first
request is now awaited for the delay read from the node's own startup log plus a margin; Amaru keeps
the normal response timeout. No release default is assumed.
"""
from __future__ import annotations

import json
import socket

import pytest

from scripts import runtime_redeemer_container_live_controls as lc
from scripts import runtime_txsubmission_probe as probe


def _runtime(tmp_path, impl):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"network_magic": 42, "nodes": [{
        "id": "node1", "name": "node1", "impl": impl, "container_name": "dwarf-x-node1-1", "container_ip": "10.9.0.2",
        "container_listen_address": "node1:3001", "port": 34301, "log_path": str(tmp_path / "node1.log"),
        "container_id": "c" * 64}]}))
    return path


class _Sock:
    def __init__(self):
        self.timeout = None
        self.sent = []

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent.append(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("impl,expected", [("cardano-node", 60 + probe.FIRST_REQUEST_MARGIN_SECONDS), ("amaru", 2.0)])
def test_first_request_is_awaited_for_the_logged_init_delay(tmp_path, monkeypatch, impl, expected):
    monkeypatch.setattr(lc, "_reference_tx_submission_propagation_gate",
                        lambda node, inspect_container: {"tx_submission_init_delay_seconds": 60})
    monkeypatch.setattr(lc, "wait_for_target_propagation_eligibility",
                        lambda *a, **k: pytest.fail("the probe must not wait for node startup"))
    sock = _Sock()
    monkeypatch.setattr(probe.socket, "create_connection", lambda *a, **k: sock)
    monkeypatch.setattr(probe, "_classify_handshake_reply", lambda payload: "accepted")
    seen = []

    def recv(s):
        seen.append(s.timeout)
        if len(seen) == 1:
            return b"\x83\x01\x0a\xa0"
        raise socket.timeout()

    monkeypatch.setattr(probe, "_recv_mux_payload", recv)
    with pytest.raises(RuntimeError, match="got timeout"):
        probe.run_probe(runtime_metadata_path=_runtime(tmp_path, impl), target_node="node1",
                        output_dir=tmp_path / "out", mode="txsubmission_window_pressure")
    assert seen == [2.0, expected]


def test_cardano_node_without_a_logged_delay_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "_reference_tx_submission_propagation_gate", lambda node, inspect_container: {})
    with pytest.raises(ValueError, match="tx_submission_init_delay_seconds"):
        probe._first_request_timeout_seconds({"impl": "cardano-node"}, 2.0)


def test_in_package_cardano_node_waits_within_the_step_budget(monkeypatch):
    """Inside the Antithesis package there is no node log or docker inspect; the first request is
    awaited for a bounded budget that stays inside the step timeout, not refused."""
    monkeypatch.setattr(lc, "_reference_tx_submission_propagation_gate",
                        lambda *a, **k: pytest.fail("no docker/log access inside the package"))
    node = {"impl": "cardano-node", "log_path": None, "observation_transport": "antithesis-package"}
    assert probe._first_request_timeout_seconds(node, 2.0) == probe.PACKAGE_FIRST_REQUEST_BUDGET_SECONDS
    assert probe.PACKAGE_FIRST_REQUEST_BUDGET_SECONDS < 240


def test_in_package_cardano_node_reads_its_logged_delay_from_the_mounted_output(tmp_path, monkeypatch):
    """F183: with a mounted log (A4 gives package nodes a log_path) the probe took the docker
    propagation-gate path and raised; it now scans the mounted output for ncTxSubmissionInitDelay
    and falls back to the step budget when the line is not there."""
    logs = tmp_path / "logs"; (logs / "node1.example_3001").mkdir(parents=True)
    node = {"impl": "cardano-node", "observation_transport": "antithesis-package", "log_path": str(logs)}
    assert probe._first_request_timeout_seconds(node, 2.0) == probe.PACKAGE_FIRST_REQUEST_BUDGET_SECONDS
    (logs / "node1.example_3001" / "node-0.json").write_text(
        '{"at":"2026-11-11T00:00:00Z","data":"ncTxSubmissionInitDelay = TxSubmissionInitDelay 60s"}\n')
    assert probe._first_request_timeout_seconds(node, 2.0) == 60.0 + probe.FIRST_REQUEST_MARGIN_SECONDS
