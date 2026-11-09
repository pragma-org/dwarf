"""F111: the controlled Plutus checks account for the workload's double-spend probes.

run_plutus_workload resubmits an already-included spend after every batch of 20 (a phase-1
invalid transaction the node must refuse). The probes have no lock transaction and are not
spends, so the identifier and accounting checks failed on every complete run (live: profile-za
f0c5c32f, 60 spends + 3 probes). Probes are now checked on their own: all must be refused.
"""
from __future__ import annotations

import json

import pytest

from scripts import runtime_controlled_plutus_transactions as subject


def _spend(kind, index, outcome, chain):
    return {"attempt_id": f"{kind}-{index}", "outcome": outcome, "chain_outcome": chain,
            "lock_transaction_id": f"lock-{kind}-{index}", "spend_transaction_id": f"spend-{kind}-{index}",
            "transaction_bytes": 111, "elapsed_nanos": 2000, "elapsed_micros": 2.0,
            "submit_to_protocol_response_nanos": 1000, "submit_to_protocol_response_micros": 1.0,
            "submit_to_block_inclusion_nanos": 2000, "submit_to_block_inclusion_micros": 2.0,
            "submit_to_chain_adoption_nanos": 2000, "submit_to_chain_adoption_micros": 2.0}


def _probe(index, outcome="rejected"):
    return {"attempt_id": f"plutus-{index:04d}-double-spend", "outcome": outcome,
            "chain_outcome": "rejected-phase1" if outcome == "rejected" else outcome,
            "spend_transaction_id": f"spend-v-{index}", "resubmitted_transaction_file": "spend.tx",
            "relay_forwarded": None, "fallback_errors": [], "transaction_bytes": 111,
            "elapsed_nanos": 1000, "elapsed_micros": 1.0,
            "submit_to_protocol_response_nanos": 1000, "submit_to_protocol_response_micros": 1.0}


def _run(monkeypatch, tmp_path, probes):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "runtime.json").write_text(json.dumps({"runtime": "fixture", "profile_id": "p",
                                                           "compose_project": "dwarf-profile-p"}))
    (runtime_root / "versioned-deployment.json").write_text(json.dumps({
        "adapter": "runtime_amaru_control_substrate.py", "lifecycle": "cardano_amaru_relay_bootstrap_control",
        "profile_id": "p", "compose_project": "dwarf-profile-p",
        "substrate": {"network_magic": 42, "controller_ownership": {"installation_id": "i", "topology_id": "p"}}}))
    states = iter([{"running": True, "restart_count": 0, "oom_killed": False}] * 2)
    tips = iter([{"block_height": 100, "hash": "aa", "slot": 100}, {"block_height": 101, "hash": "bb", "slot": 101}])
    monkeypatch.setattr(subject, "_target", lambda _r: ({}, {"container_name": "t", "container_socket_path": "/s",
                                                            "network_magic": 42}))
    monkeypatch.setattr(subject, "_container_state", lambda _c: next(states))
    monkeypatch.setattr(subject, "_query_cardano_tip", lambda _c, *, socket_path, network_magic: next(tips))
    monkeypatch.setattr(subject, "_container_logs", lambda _c, *, since: "Chain extended")
    records = ([_spend("v", i, "accepted", "included-valid") for i in range(30)]
               + [_spend("i", i, "rejected", "included-invalid") for i in range(30)] + probes)
    monkeypatch.setattr(subject, "run_plutus_workload", lambda **_k: {"records": records})
    return subject.run_controlled_plutus_transactions(runtime_root=runtime_root, output_dir=tmp_path / "out",
                                                      transaction_count=60)


def test_refused_double_spend_probes_keep_the_run_complete(monkeypatch, tmp_path):
    result = _run(monkeypatch, tmp_path, [_probe(19), _probe(39), _probe(59)])
    checks = result["checks"]
    assert checks["transaction_identifiers_retained"] is True
    assert checks["workload_accounting_complete"] is True
    assert checks["double_spend_probes_rejected"] is True
    assert result["outcome_counts"] == {"accepted": 30, "rejected": 30, "double_spend_probes_rejected": 3}


@pytest.mark.parametrize("outcome", ["accepted-unexpected", "submit-error"])
def test_a_double_spend_that_is_not_refused_fails_its_own_check(monkeypatch, tmp_path, outcome):
    result = _run(monkeypatch, tmp_path, [_probe(19), _probe(39, outcome), _probe(59)])
    assert result["checks"]["double_spend_probes_rejected"] is False
    assert result["checks"]["workload_accounting_complete"] is True


def test_a_spend_without_its_lock_transaction_still_fails_identifiers(monkeypatch, tmp_path):
    broken = _spend("v", 99, "accepted", "included-valid")
    broken["lock_transaction_id"] = None
    result = _run(monkeypatch, tmp_path, [broken])
    assert result["checks"]["transaction_identifiers_retained"] is False
