import json
from pathlib import Path

import pytest

from profile_manager import primitives as primitive_module


class _Runtime:
    def __init__(self):
        self.markers = []

    def mark_phase(self, phase_id, state):
        marker = {"phase_id": phase_id, "state": state, "epoch_seconds": len(self.markers) + 1.0}
        self.markers.append(marker)
        return marker


class _Handle:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True)
        self._measurement_runtime = _Runtime()
        self.logs = []

    def log(self, **entry):
        self.logs.append(entry)


def _primitive(class_name, params=None):
    assert hasattr(primitive_module, class_name), f"missing primitive class {class_name}"
    return getattr(primitive_module, class_name)(params=params or {})


def _tip(height):
    return {"block_height": height, "hash": f"{height:064x}", "slot": height * 2}


def test_amaru_runtime_target_uses_the_consumer_node_socket(monkeypatch, tmp_path):
    metadata_path = tmp_path / "runtime.json"
    metadata_path.write_text(
        json.dumps(
            {
                "identity": {
                    "services": {
                        "amaru-relay-1": {"container": "amaru-target"},
                        "amaru-consumer": {"container": "amaru-consumer"},
                    }
                }
            }
        )
    )
    handle = _Handle(tmp_path / "run")
    monkeypatch.setattr(
        primitive_module,
        "_measurement_target_identity",
        lambda _handle: {"implementation": "amaru"},
    )
    monkeypatch.setattr(
        primitive_module,
        "_protocol_docker_result",
        lambda *_args, **_kwargs: type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "NetworkSettings": {
                                "Networks": {"profile": {"IPAddress": "10.0.0.2"}}
                            }
                        }
                    ]
                ),
            },
        )(),
    )

    target = primitive_module._resolve_client_runtime_target(
        handle, {"runtime_metadata_path": str(metadata_path)}
    )

    assert target["peer"]["socket_path"] == "/state/node.socket"


def test_wait_for_chain_progress_retains_observed_positive_delta(monkeypatch, tmp_path):
    handle = _Handle(tmp_path / "run")
    tips = iter([_tip(100), _tip(101), _tip(105)])
    monkeypatch.setattr(primitive_module, "_resolve_client_runtime_target", lambda *_: {"id": "node1"})
    monkeypatch.setattr(primitive_module, "_observe_client_target_tip", lambda *_: next(tips))
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    _primitive("RuntimeWaitForChainProgress", {"minimum_blocks": 5, "poll_interval_seconds": 0}).run(handle, None)

    proof = json.loads((handle.run_dir / "outputs/client-example-proof/chain-progress-readiness.json").read_text())
    assert proof["checks"]["minimum_progress_observed"] is True
    assert proof["start_tip"]["block_height"] == 100
    assert proof["end_tip"]["block_height"] == 105


def test_controlled_chain_window_correlates_exact_adopted_blocks_and_applications(monkeypatch, tmp_path):
    handle = _Handle(tmp_path / "run")
    adopted = [{**_tip(height), "observed_at": f"2026-09-20T00:00:{height - 100:02d}Z"} for height in range(101, 132)]
    applications = [
        {"sample_id": f"apply-{index:04d}", "duration_micros": index + 1, "observed_at": row["observed_at"]}
        for index, row in enumerate(adopted)
    ]
    monkeypatch.setattr(primitive_module, "_resolve_client_runtime_target", lambda *_: {"id": "node1"})
    monkeypatch.setattr(primitive_module, "_protocol_container_state", lambda *_: observed_health["before"])
    monkeypatch.setattr(primitive_module, "_observe_client_target_tip", lambda *_: _tip(132))
    monkeypatch.setattr(primitive_module, "_collect_controlled_block_evidence", lambda *_args, **_kwargs: (adopted, applications, []))
    observed_health = {
        "before": {"running": True, "restart_count": 7, "oom_killed": False},
        "after": {"running": True, "restart_count": 7, "oom_killed": False},
        "tip_after": _tip(132),
        "log_signals": {"fatal": [], "background": []},
    }
    monkeypatch.setattr(
        primitive_module, "_observe_client_window_health", lambda *_args, **_kwargs: observed_health)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    _primitive("RuntimeControlledChainProgressWindow", {"warm_up_seconds": 0, "duration_seconds": 0, "minimum_adopted_blocks": 30}).run(handle, None)

    proof = json.loads((handle.run_dir / "outputs/client-example-proof/controlled-chain-progress.json").read_text())
    assert proof["checks"] == {
        "application_samples_correlated": True,
        "all_application_samples_correlated": True,
        "candidate_application_alignment": True,
        "minimum_adopted_block_range_observed": True,
        "monotonic_height": True,
    }
    assert len(proof["adopted_blocks"]) == 31
    assert len(proof["correlations"]) == 31
    assert {row["block_hash"] for row in proof["correlations"]} == {row["hash"] for row in adopted}
    assert proof["target_health"]["before"]["restart_count"] == 7
    assert handle._measurement_runtime.markers == [
        {"phase_id": "controlled-chain-progress", "state": "start", "epoch_seconds": 1.0},
        {"phase_id": "controlled-chain-progress", "state": "end", "epoch_seconds": 2.0},
    ]


def test_cardano_block_evidence_excludes_rejected_applications_and_keeps_nanoseconds(tmp_path):
    log_path = tmp_path / "node.json"
    measurement_path = tmp_path / "measurement.ndjson"
    log_path.write_text(
        json.dumps(
            {
                "at": "2026-09-20T00:00:01Z",
                "ns": "ChainDB.AddBlockEvent.AddedToCurrentChain",
                "data": {
                    "headers": [
                        {"blockNo": 101, "hash": "a" * 64, "slotNo": 202}
                    ]
                },
            }
        )
        + "\n"
    )
    measurement_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "ledger_stage",
                        "stage": "block-application",
                        "outcome": "rejected",
                        "elapsed_nanos": 9999,
                        "duration_us": 9,
                    }
                ),
                json.dumps(
                    {
                        "event": "ledger_stage",
                        "stage": "block-application",
                        "outcome": "accepted",
                        "elapsed_nanos": 2184,
                        "duration_us": 2,
                    }
                ),
            ]
        )
        + "\n"
    )

    adopted, applications, excluded = primitive_module._collect_controlled_block_evidence(
        {
            "implementation": "cardano-node",
            "log_path": str(log_path),
            "measurement_log_path": str(measurement_path),
        },
        log_offset=0,
        measurement_offset=0,
    )

    assert len(adopted) == 1
    assert applications == [
        {
            "sample_id": "apply-0001",
            "duration_micros": 2.184,
            "ended_monotonic_ns": None,
            "outcome": "accepted",
            "elapsed_nanos": 2184,
        }
    ]
    assert excluded == []


def test_cardano_block_evidence_keeps_only_exact_adopted_candidate_timings(tmp_path):
    log_path = tmp_path / "node.json"
    measurement_path = tmp_path / "measurement.ndjson"
    adopted_hashes = ["a" * 64, "c" * 64]
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2026-09-20T00:00:01Z",
                        "ns": "ChainDB.AddBlockEvent.AddBlockValidation.ValidCandidate",
                        "data": {"block": f"{'a' * 64}@201"},
                    }
                ),
                json.dumps(
                    {
                        "at": "2026-09-20T00:00:02Z",
                        "ns": "ChainDB.AddBlockEvent.AddBlockValidation.ValidCandidate",
                        "data": {"block": f"{'b' * 64}@202"},
                    }
                ),
                json.dumps(
                    {
                        "at": "2026-09-20T00:00:03Z",
                        "ns": "ChainDB.AddBlockEvent.AddBlockValidation.ValidCandidate",
                        "data": {"block": f"{'c' * 64}@203"},
                    }
                ),
                *[
                    json.dumps(
                        {
                            "at": f"2026-09-20T00:00:0{index + 4}Z",
                            "ns": "ChainDB.AddBlockEvent.AddedToCurrentChain",
                            "data": {
                                "headers": [
                                    {
                                        "blockNo": 101 + index,
                                        "hash": block_hash,
                                        "slotNo": 201 + (index * 2),
                                    }
                                ]
                            },
                        }
                    )
                    for index, block_hash in enumerate(adopted_hashes)
                ],
            ]
        )
        + "\n"
    )
    measurement_path.write_text(
        "\n".join(
            json.dumps(
                {
                    "event": "ledger_stage",
                    "stage": "block-application",
                    "outcome": "accepted",
                    "elapsed_nanos": elapsed,
                    "duration_us": elapsed // 1000,
                }
            )
            for elapsed in (2184, 3300, 4701)
        )
        + "\n"
    )

    adopted, applications, excluded = primitive_module._collect_controlled_block_evidence(
        {
            "implementation": "cardano-node",
            "log_path": str(log_path),
            "measurement_log_path": str(measurement_path),
        },
        log_offset=0,
        measurement_offset=0,
    )

    assert [row["hash"] for row in adopted] == adopted_hashes
    assert [row["block_hash"] for row in applications] == adopted_hashes
    assert [row["duration_micros"] for row in applications] == [2.184, 4.701]
    assert excluded == [
        {
            "sample_id": "apply-0001",
            "block_hash": "b" * 64,
            "block_slot": 202,
            "duration_micros": 3.3,
            "elapsed_nanos": 3300,
            "reason": "valid-candidate-not-adopted-in-controlled-window",
        }
    ]


def test_controlled_chain_window_fails_when_an_application_is_unpaired(monkeypatch, tmp_path):
    handle = _Handle(tmp_path / "run")
    adopted = [{**_tip(height), "observed_at": str(height)} for height in range(101, 131)]
    applications = [
        {"sample_id": f"apply-{index:04d}", "duration_micros": 1.0}
        for index in range(31)
    ]
    monkeypatch.setattr(primitive_module, "_resolve_client_runtime_target", lambda *_: {"id": "node1"})
    monkeypatch.setattr(primitive_module, "_protocol_container_state", lambda *_: {"running": True})
    monkeypatch.setattr(primitive_module, "_observe_client_target_tip", lambda *_: _tip(131))
    monkeypatch.setattr(primitive_module, "_collect_controlled_block_evidence", lambda *_args, **_kwargs: (adopted, applications, []))
    monkeypatch.setattr(primitive_module, "_observe_client_window_health", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="controlled chain progress proof failed"):
        _primitive(
            "RuntimeControlledChainProgressWindow",
            {"warm_up_seconds": 0, "duration_seconds": 0, "minimum_adopted_blocks": 30},
        ).run(handle, None)

    proof = json.loads(
        (handle.run_dir / "outputs/client-example-proof/controlled-chain-progress.json").read_text()
    )
    assert proof["checks"]["all_application_samples_correlated"] is False
def test_real_restart_emits_three_ordered_observed_readiness_gates(monkeypatch, tmp_path):
    handle = _Handle(tmp_path / "run")
    target = {"id": "node1", "container": "node1", "listen_host": "10.0.0.2", "listen_port": 3001}
    tips = iter([_tip(100), _tip(106)])
    monkeypatch.setattr(primitive_module, "_resolve_client_runtime_target", lambda *_: target)
    monkeypatch.setattr(primitive_module, "_observe_client_target_tip", lambda *_: next(tips))
    monkeypatch.setattr(primitive_module, "_restart_client_runtime_target", lambda *_: {"command": "docker restart node1", "exit_code": 0})
    monkeypatch.setattr(primitive_module, "_client_listener_ready", lambda *_: True)
    monkeypatch.setattr(primitive_module, "_client_peer_role_ready", lambda *_: {"ready": True, "peer_id": "node2"})
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    _primitive("RuntimeRealTargetRestartAndReadiness", {"poll_interval_seconds": 0, "timeout_seconds": 5}).run(handle, None)

    proof = json.loads((handle.run_dir / "outputs/client-example-proof/restart-readiness.json").read_text())
    assert proof["checks"]["all_readiness_gates_observed"] is True
    assert proof["checks"]["readiness_gates_ordered"] is True
    hooks = [json.loads(line) for line in (handle.run_dir / "events/target-hooks.ndjson").read_text().splitlines()]
    assert [row["event"] for row in hooks] == ["restart_started", "listener_ready", "chain_progress_ready", "peer_role_ready"]


def test_controlled_sync_range_retains_exact_start_and_end(monkeypatch, tmp_path):
    handle = _Handle(tmp_path / "run")
    target = {"id": "node1"}
    tips = iter([_tip(200), _tip(202), _tip(205)])
    monkeypatch.setattr(primitive_module, "_protocol_container_state", lambda *_: observed_health["before"])
    monkeypatch.setattr(primitive_module, "_resolve_client_runtime_target", lambda *_: target)
    monkeypatch.setattr(primitive_module, "_observe_client_target_tip", lambda *_: next(tips))
    observed_health = {
        "before": {"running": True, "restart_count": 1, "oom_killed": False},
        "after": {"running": True, "restart_count": 1, "oom_killed": False},
        "tip_after": _tip(205),
        "log_signals": {"fatal": [], "background": []},
    }
    monkeypatch.setattr(
        primitive_module, "_observe_client_window_health", lambda *_args, **_kwargs: observed_health)
    monkeypatch.setattr(primitive_module, "_client_peer_role_ready", lambda *_: {"ready": True, "usable": True, "peer_id": "node2"})
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    _primitive("RuntimeControlledSyncRange", {"minimum_blocks": 5, "poll_interval_seconds": 0, "peer_policy": "three-node-controlled-local-mesh"}).run(handle, None)

    proof = json.loads((handle.run_dir / "outputs/client-example-proof/controlled-sync-range.json").read_text())
    assert proof["checks"]["controlled_sync_range_complete"] is True
    assert proof["start"]["block_height"] == 200
    assert proof["end"]["block_height"] == 205
    assert proof["peer_policy"] == "three-node-controlled-local-mesh"
    assert [row["event"] for row in proof["range_events"]] == [
        "sync_range_started",
        "sync_range_completed",
    ]
    assert proof["range_events"][0]["tip"]["block_height"] == 200
    assert proof["range_events"][1]["tip"]["block_height"] == 205
    assert proof["range_events"][1]["elapsed_seconds"] > proof["range_events"][0]["elapsed_seconds"]

    assert proof["target_health"]["before"]["restart_count"] == 1

def test_g3b_primitives_and_assertions_are_registered_with_schemas():
    root = Path(__file__).resolve().parents[1]
    registry = primitive_module.load_registry(root / "dwarf/primitives/registry.json")
    expected = {
        "runtime_wait_for_chain_progress": "setup",
        "runtime_controlled_chain_progress_window": "load",
        "runtime_real_target_restart_and_readiness": "load",
        "runtime_controlled_sync_range": "load",
        "minimum_adopted_block_range_observed": "assertion",
        "block_application_samples_correlated": "assertion",
        "restart_readiness_complete": "assertion",
        "controlled_sync_range_complete": "assertion",
    }
    for name, family in expected.items():
        assert name in registry
        assert registry[name].family == family
        assert registry[name].params_schema
        assert (root / "dwarf" / registry[name].params_schema).is_file()


def test_g3b_final_scenarios_match_frozen_legs():
    root = Path(__file__).resolve().parents[1]
    expected = {
        "client-example-block-application-amaru": "amaru",
        "client-example-block-application-cardano": "cardano-node",
        "client-example-restart-recovery-sync-amaru": "amaru",
        "client-example-restart-recovery-sync-cardano": "cardano-node",


    }
    for scenario_id, implementation in expected.items():
        body = json.loads((root / "dwarf/scenarios" / f"{scenario_id}.yaml").read_text())
        assert body["id"] == scenario_id
        assert body["target"]["implementation"] == implementation
        assert body["evidence_intent"] == "candidate"
        assert body["promotion_blockers"]
        exact = {
            "amaru": (
                "sha256:c3f139e87b4ada079a4dc5c656a2ca06c6dc30ea55719d54bedb772c836de862",
                "4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0",
            ),
            "cardano-node": (
                "sha256:956ae21cf9141a7149692392453ea31ece00e33960c2cca0f79548beeacf7370",
                "1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c",
            ),
        }
        assert body["setup"][0]["image_digest"] == exact[implementation][0]
        assert body["setup"][0]["patch_set_sha256"] == exact[implementation][1]
        health_probe = next(
            item for item in body["probes"]
            if item["primitive"] == "runtime_target_health_and_progress"
        )
        assert health_probe["progress_reference"] == "load-start"
        if "restart-recovery-sync" in scenario_id:
            resource_id = f"{implementation.split('-')[0]}-stock-resources"
            resource_override = next(
                item for item in body["measurements"]
                if item["id"] == resource_id
            )
            assert resource_override["parameters"]["sample_interval_seconds"] == 0.25


def test_block_application_assertion_rejects_unpaired_retained_sample(tmp_path):
    handle = _Handle(tmp_path / "run")
    proof_dir = handle.run_dir / "outputs/client-example-proof"
    proof_dir.mkdir(parents=True)
    correlations = [
        {
            "block_hash": f"{index:064x}",
            "application_sample_id": f"apply-{index:04d}",
            "duration_micros": 1.0,
        }
        for index in range(30)
    ]
    (proof_dir / "controlled-chain-progress.json").write_text(
        json.dumps(
            {
                "target_node": "node1",
                "start_marker": {"state": "start"},
                "end_marker": {"state": "end"},
                "application_samples": [
                    {"sample_id": f"apply-{index:04d}"} for index in range(31)
                ],
                "correlations": correlations,
            }
        )
    )

    result = _primitive(
        "BlockApplicationSamplesCorrelated", {"minimum_samples": 30}
    ).evaluate(handle)

    assert result["result"] == "fail"
    assert result["evaluated_value"]["all_application_samples_correlated"] is False


def test_controlled_sync_assertion_requires_exact_timed_range_events(tmp_path):
    handle = _Handle(tmp_path / "run")
    proof_dir = handle.run_dir / "outputs/client-example-proof"
    proof_dir.mkdir(parents=True)
    (proof_dir / "controlled-sync-range.json").write_text(
        json.dumps(
            {
                "target_node": "node1",
                "minimum_blocks": 5,
                "peer_policy": "mesh",
                "start": _tip(200),
                "end": _tip(205),
                "range_events": [],
            }
        )
    )

    result = _primitive(
        "ControlledSyncRangeComplete", {"minimum_blocks": 5}
    ).evaluate(handle)

    assert result["result"] == "fail"
    assert result["evaluated_value"]["timed_range_events_complete"] is False


def test_restart_readiness_assertion_rechecks_raw_gate_order(tmp_path):
    handle = _Handle(tmp_path / "run")
    proof_dir = handle.run_dir / "outputs/client-example-proof"
    proof_dir.mkdir(parents=True)
    (proof_dir / "restart-readiness.json").write_text(
        json.dumps(
            {
                "target_node": "node1",
                "restart": {
                    "event": "restart_started",
                    "target_node": "node1",
                    "elapsed_seconds": 10.0,
                },
                "gates": [],
                "checks": {
                    "all_readiness_gates_observed": True,
                    "readiness_gates_ordered": True,
                    "target_progressed_after_restart": True,
                },
            }
        )
    )

    result = _primitive("RestartReadinessComplete").evaluate(handle)

    assert result["result"] == "fail"
    assert result["evaluated_value"]["observed_readiness_gates_complete"] is False
