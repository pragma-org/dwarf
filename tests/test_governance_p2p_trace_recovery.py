import hashlib
import json
from pathlib import Path

import pytest


def _target(log_path: Path) -> dict:
    return {
        "id": "node1",
        "container_id": "a" * 64,
        "container_image_id": "sha256:" + "b" * 64,
        "source_revision": "c" * 40,
        "container_peer_addresses": ["node2:3001"],
        "log_path": str(log_path),
    }


def test_governance_p2p_trace_is_opt_in_and_semantically_bound():
    from profile_manager.scenario import semantic_validate_scenario
    from scripts.runtime_compose_substrate import _docker_compose_body

    root = Path(__file__).resolve().parents[1]
    scenario_path = root / (
        "dwarf/scenarios/"
        "governance-zero-denominator-admission-differential-amaru-cardano-node.yaml"
    )
    result = semantic_validate_scenario(
        scenario_path, registry_path=root / "dwarf/primitives/registry.json"
    )
    assert result["errors"] == []
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    assert scenario["substrate"]["amaru_txsubmission_trace"] is True
    governance = next(
        item
        for item in scenario["load"]
        if item["primitive"] == "runtime_governance_zero_denominator_admission"
    )
    assert governance["p2p_trace_recovery"] is True

    node = {
        "id": "node1",
        "impl": "amaru",
        "version": "10.11.test",
        "role": "submitter",
        "listen_address": "127.0.0.1:5001",
        "chain_dir": "/tmp/chain",
        "ledger_dir": "/tmp/ledger",
        "container_peer_addresses": ["node2:3001"],
        "image": "sha256:" + "d" * 64,
    }
    traced = _docker_compose_body(
        compose_project="trace-test",
        nodes=[node],
        network_name="testnet_42",
        amaru_txsubmission_trace=True,
    )["services"]["node1"]["environment"]
    assert traced["AMARU_TRACE_EMIT_PRIVATE"] == "true"
    assert "amaru::protocols=debug" in traced["AMARU_LOG"].split(",")

    ordinary = _docker_compose_body(
        compose_project="ordinary-test",
        nodes=[node],
        network_name="testnet_42",
    )["services"]["node1"]["environment"]
    assert "AMARU_TRACE_EMIT_PRIVATE" not in ordinary
    assert "amaru::protocols=debug" not in ordinary["AMARU_LOG"].split(",")


def test_governance_trace_binds_exact_cycle_and_rejects_ambiguous_prefix(tmp_path):
    from scripts.runtime_governance_zero_denominator import (
        assess_txsubmission_trace,
    )

    mutant = "1a2b3c4d5e6f" + "1" * 52
    later = "abcdef123456" + "2" * 52
    log_path = tmp_path / "node1.log"
    prelude = (
        "INFO amaru::protocols: manager.peer.connected "
        "peer=node2:3001 conn_id=41\n"
    )
    log_path.write_text(prelude, encoding="utf-8")
    start = log_path.stat().st_size
    trace = "\n".join(
        [
            "DEBUG amaru::protocols: tx_submission.initiator.reply_tx_ids "
            f"peer=node2:3001 count=2 ids=[{mutant}, {later}]",
            "DEBUG amaru::protocols: tx_submission.initiator.received_request "
            f'request=txs count=1 ids="[{mutant[:12]}]" peer=node2:3001',
            "DEBUG amaru::protocols: tx_submission.initiator.reply_txs "
            "peer=node2:3001 count=1",
            "INFO amaru::protocols: connection.child_died "
            "peer=node2:3001 conn_id=41 child=TxSubmissionInitiator",
            "INFO amaru::protocols: manager.peer.connection_died "
            "peer=node2:3001 conn_id=41 role=initiator",
            "INFO amaru::protocols: manager.peer.connected "
            "peer=node2:3001 conn_id=42",
            "DEBUG amaru::protocols: tx_submission.initiator.reply_tx_ids "
            f"peer=node2:3001 count=2 ids=[{mutant}, {later}]",
        ]
    ) + "\n"
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(trace)

    evidence = assess_txsubmission_trace(
        target=_target(log_path),
        log_start_offset=start,
        malformed_transaction_id=mutant,
        later_valid_transaction_id=later,
    )
    assert evidence["status"] == "exact-cycle-observed"
    assert evidence["restart_eligible"] is True
    assert evidence["malformed"]["advertised"] is True
    assert evidence["malformed"]["requested"] is True
    assert evidence["malformed"]["reply_txs_observed"] is True
    assert evidence["malformed"]["acknowledged_before_close"] is False
    assert evidence["later_valid"]["advertised"] is True
    assert evidence["later_valid"]["requested"] is False
    assert evidence["connection"]["closed_connection_id"] == 41
    assert evidence["connection"]["reconnected_connection_id"] == 42
    assert evidence["malformed"]["readvertised_after_reconnect"] is True
    assert evidence["log_slice_sha256"] == hashlib.sha256(
        trace.encode("utf-8")
    ).hexdigest()

    colliding = mutant[:12] + "f" * 52
    ambiguous_path = tmp_path / "ambiguous.log"
    ambiguous_path.write_text(
        prelude
        + trace.replace(
            f"ids=[{mutant}, {later}]",
            f"ids=[{mutant}, {colliding}, {later}]",
            1,
        ),
        encoding="utf-8",
    )
    ambiguous = assess_txsubmission_trace(
        target=_target(ambiguous_path),
        log_start_offset=len(prelude.encode("utf-8")),
        malformed_transaction_id=mutant,
        later_valid_transaction_id=later,
    )
    assert ambiguous["status"] == "inconclusive"
    assert ambiguous["restart_eligible"] is False
    assert ambiguous["blocking_reason"] == "requested-id-prefix-is-ambiguous"


def test_governance_recovery_is_conditional_and_reuses_exact_valid_bytes(
    monkeypatch, tmp_path
):
    from scripts import runtime_governance_zero_denominator as governance

    payload = b"exact-post-control"
    transaction_id = "d" * 64
    log_path = tmp_path / "node1.log"
    log_path.write_text("startup\n", encoding="utf-8")
    target = _target(log_path)
    reference = {
        "id": "node2",
        "container_id": "e" * 64,
        "container_image_id": "sha256:" + "f" * 64,
        "source_revision": "1" * 40,
        "container_socket_path": "/env/socket/node2/sock",
    }
    events = []

    monkeypatch.setattr(
        governance,
        "_reference_peer_identity",
        lambda _reference: {"peer": "172.30.0.3:3001"},
    )
    monkeypatch.setattr(
        governance,
        "_restart_owned_amaru",
        lambda **_kwargs: events.append("restart")
        or {"status": "restarted", "log_start_offset": 8},
    )
    monkeypatch.setattr(
        governance,
        "_wait_for_fresh_peer_connection",
        lambda **_kwargs: events.append("reconnect")
        or {"status": "connected", "conn_id": 77},
    )
    monkeypatch.setattr(
        governance,
        "_query_reference_tip",
        lambda **_kwargs: {"block": 9, "slot": 99, "hash": "2" * 64},
    )
    monkeypatch.setattr(
        governance,
        "_wait_for_reference_tip_progress",
        lambda **_kwargs: {
            "status": "progressed",
            "progressed_tip": {"block": 10, "slot": 100, "hash": "3" * 64},
        },
    )
    monkeypatch.setattr(
        governance,
        "_wait_for_target_adoption",
        lambda **kwargs: events.append(
            ("adopt", kwargs.get("minimum_log_offset"))
        )
        or {"status": "adopted"},
    )

    def submit(*, target, payload: bytes, transaction_id: str, **_kwargs):
        events.append(("submit", payload, transaction_id))
        return {"classification": "accepted"}

    monkeypatch.setattr(governance, "_submit_amaru_with_admission_log", submit)
    monkeypatch.setattr(
        governance,
        "_wait_for_reference_inclusion",
        lambda **_kwargs: events.append("included") or {"status": "included"},
    )
    monkeypatch.setattr(
        governance,
        "_terminal_inclusion_tip",
        lambda *_args, **_kwargs: {
            "block": 11,
            "slot": 101,
            "hash": "4" * 64,
        },
    )

    not_run = governance._recover_post_control_after_cycle(
        trace_evidence={"status": "inconclusive", "restart_eligible": False},
        runtime={"network_magic": 42},
        target=target,
        reference=reference,
        payload=payload,
        transaction_id=transaction_id,
        target_propagation_gate={"status": "validated"},
        cli="cardano-cli",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
        address="addr_test1",
        output_dir=tmp_path / "not-run",
    )
    assert not_run["status"] == "not-attempted"
    assert events == []

    recovered = governance._recover_post_control_after_cycle(
        trace_evidence={
            "status": "exact-cycle-observed",
            "restart_eligible": True,
            "connection": {"reconnected_connection_id": 42},
        },
        runtime={"network_magic": 42},
        target=target,
        reference=reference,
        payload=payload,
        transaction_id=transaction_id,
        target_propagation_gate={"status": "validated"},
        cli="cardano-cli",
        socket_path="/env/socket/node2/sock",
        network_magic=42,
        address="addr_test1",
        output_dir=tmp_path / "recovery",
    )
    assert recovered["status"] == "recovered"
    assert recovered["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert recovered["transaction_id"] == transaction_id
    assert events.count("restart") == 1
    assert ("submit", payload, transaction_id) in events
    assert "included" in events
    assert ("adopt", 8) in events
    assert governance._post_mutant_control_complete(
        {
            "target": {
                "classification": "accepted",
                "local_mempool_admission_observed": True,
                "transaction_id": transaction_id,
                "body_sha256": "5" * 64,
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "funding_input": "6" * 64 + "#7",
                "included": False,
                "target_adopted": False,
                "propagation_observation": {
                    "status": "not-included-after-reference-progress",
                    "terminal_progress_block_threshold": 3,
                },
            },
            "conditional_recovery": recovered,
        }
    ) is True
