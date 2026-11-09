"""F119: the tx-submission probe waits out cardano-node's tx-submission init delay first.

cardano-node asks peers for transactions only ncTxSubmissionInitDelay after startup. The probe
connected soon after the substrate came up and waited 2 s for MsgRequestTxIds (live: 1824beb1,
"did not produce a data response; got timeout"). It now reuses the F97c gate: the delay read from
the node's own startup log, then a bounded wait. Amaru targets are not delayed.
"""
from __future__ import annotations

import json

import pytest

from scripts import runtime_redeemer_container_live_controls as lc
from scripts import runtime_txsubmission_probe as probe


class _Stop(Exception):
    pass


def _runtime(tmp_path, impl):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"network_magic": 42, "nodes": [{
        "id": "node1", "name": "node1", "impl": impl, "container_name": "dwarf-x-node1-1", "container_ip": "10.9.0.2",
        "container_listen_address": "node1:3001", "port": 34301, "log_path": str(tmp_path / "node1.log"),
        "container_id": "c" * 64}]}))
    return path


@pytest.mark.parametrize("impl,waits", [("cardano-node", True), ("amaru", False)])
def test_probe_waits_for_the_logged_init_delay_before_connecting(tmp_path, monkeypatch, impl, waits):
    order = []
    monkeypatch.setattr(lc, "_reference_tx_submission_propagation_gate",
                        lambda node, inspect_container: order.append("gate") or {"eligible_at_epoch_seconds": 1.0,
                                                                                 "tx_submission_init_delay_seconds": 60})
    monkeypatch.setattr(lc, "wait_for_target_propagation_eligibility", lambda gate, **k: order.append("wait") or 0.0)

    def connect(*args, **kwargs):
        order.append("connect")
        raise _Stop()

    monkeypatch.setattr(probe.socket, "create_connection", connect)
    with pytest.raises(_Stop):
        probe.run_probe(runtime_metadata_path=_runtime(tmp_path, impl), target_node="node1",
                        output_dir=tmp_path / "out", mode="txsubmission_window_pressure")
    assert order == (["gate", "wait", "connect"] if waits else ["connect"])
