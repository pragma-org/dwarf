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
        "canonical_chain_progress_complete": "assertion",
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
        "client-example-block-application-amaru-canonical-v2": "amaru",
        "client-example-block-application-amaru-canonical-v3": "amaru",
        "client-example-block-application-cardano-canonical-v2": "cardano-node",
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
        if scenario_id == "client-example-block-application-amaru-canonical-v3":
            exact["amaru"] = (
                "sha256:d120f9515d5bcc6aa68629e0370fa7bdf5a5612e5d35005aa231d32ab2b7169a",
                "042f6b1840bc6a30e65d77ce702e1be9967b77564ecfb9c77c1e5c25520aad00",
            )
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
        if "canonical-v2" in scenario_id or "canonical-v3" in scenario_id:
            window = next(
                item for item in body["load"]
                if item["primitive"] == "runtime_controlled_chain_progress_window"
            )
            assert window["progress_contract"] == "canonical-progress-v2"
            assert body["assertions"][0]["primitive"] == (
                "canonical_chain_progress_complete"
            )


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


def _canonical(events, *, end=None, **overrides):
    params = {
        "minimum_blocks": 30,
        "max_oscillation_episodes": 3,
        "max_oscillation_transitions": 4,
        "minimum_convergence_blocks": 3,
    }
    params.update(overrides)
    return primitive_module._derive_canonical_progress(
        events,
        start_tip=_tip(100),
        end_tip=end or events[-1],
        **params,
    )


def _healthy_window():
    return {
        "before": {"running": True, "restart_count": 2, "oom_killed": False},
        "after": {"running": True, "restart_count": 2, "oom_killed": False},
        "log_signals": {"fatal": [], "background": []},
    }


def _complete_correlations(count=30):
    return [
        {
            "block_hash": f"{index + 101:064x}",
            "application_sample_id": f"apply-{index:04d}",
            "duration_micros": 2.184,
        }
        for index in range(count)
    ]


def test_canonical_progress_v2_accepts_straight_progress():
    progress = _canonical([_tip(height) for height in range(101, 136)])

    assert progress["height_delta"] == 35
    assert progress["checks"] == {
        "bounded_canonical_progress": True,
        "final_convergence": True,
        "oscillation_within_bounds": True,
        "terminal_identity_matches": True,
    }
    assert progress["oscillation_episodes"] == []


def test_canonical_progress_v2_accepts_end_tip_ahead_of_last_window_event():
    events = [_tip(height) for height in range(101, 135)]

    progress = _canonical(events, end=_tip(135))

    assert progress["terminal_tip_relation"] == "ahead"
    assert progress["checks"]["terminal_identity_matches"] is True
    assert progress["checks"]["final_convergence"] is True


def test_canonical_progress_v2_keeps_and_accepts_bounded_same_height_switch():
    events = [_tip(height) for height in range(101, 111)]
    events.append({**_tip(110), "hash": "f" * 64})
    events.extend(_tip(height) for height in range(111, 136))

    progress = _canonical(events)

    switches = [
        row for row in progress["raw_chain_selection"]
        if row["transition"] == "same-height-hash-switch"
    ]
    assert len(switches) == 1
    assert progress["checks"]["final_convergence"] is True
    assert progress["checks"]["oscillation_within_bounds"] is True


def test_canonical_progress_v2_accepts_rollback_then_recovery():
    events = [_tip(height) for height in range(101, 116)]
    events.extend(
        {**_tip(height), "hash": f"{height + 1000:064x}"}
        for height in range(112, 136)
    )

    progress = _canonical(events)

    assert any(
        row["transition"] == "rollback"
        for row in progress["raw_chain_selection"]
    )
    assert progress["checks"]["final_convergence"] is True
    assert progress["checks"]["bounded_canonical_progress"] is True


def test_canonical_progress_v2_rejects_excessive_oscillation():
    events = [_tip(height) for height in range(101, 111)]
    events.extend(
        {**_tip(110), "hash": f"{index + 9000:064x}"}
        for index in range(6)
    )
    events.extend(_tip(height) for height in range(111, 136))

    progress = _canonical(events)

    assert progress["oscillation_transition_count"] == 6
    assert progress["checks"]["oscillation_within_bounds"] is False


def test_canonical_progress_v2_rejects_continuing_oscillation():
    events = [_tip(height) for height in range(101, 135)]
    events.append({**_tip(134), "hash": "e" * 64})

    progress = _canonical(events, end=events[-1])

    assert progress["trailing_advance_count"] == 0
    assert progress["checks"]["final_convergence"] is False


def test_canonical_progress_v2_rejects_non_converged_terminal_identity():
    events = [_tip(height) for height in range(101, 136)]
    end = {**_tip(135), "hash": "d" * 64}

    progress = _canonical(events, end=end)

    assert progress["checks"]["terminal_identity_matches"] is False
    assert progress["checks"]["final_convergence"] is False


def test_canonical_progress_v2_rejects_end_tip_behind_window_selection():
    events = [_tip(height) for height in range(101, 136)]

    progress = _canonical(events, end=_tip(134))

    assert progress["terminal_tip_relation"] == "behind"
    assert progress["checks"]["terminal_identity_matches"] is False
    assert progress["checks"]["final_convergence"] is False


def test_canonical_progress_v2_rejects_no_progress():
    events = [_tip(height) for height in range(101, 121)]

    progress = _canonical(events)

    assert progress["height_delta"] == 20
    assert progress["checks"]["bounded_canonical_progress"] is False


def test_canonical_progress_v2_rejects_missing_required_correlations():
    progress = _canonical([_tip(height) for height in range(101, 136)])

    checks = primitive_module._canonical_progress_checks(
        progress,
        correlations=_complete_correlations(29),
        minimum_correlations=30,
        target_health=_healthy_window(),
    )

    assert checks["complete_required_correlations"] is False
    assert checks["canonical_chain_progress_complete"] is False


def test_canonical_progress_v2_rejects_fatal_health_signal():
    progress = _canonical([_tip(height) for height in range(101, 136)])
    health = _healthy_window()
    health["log_signals"]["fatal"] = ["panic"]

    checks = primitive_module._canonical_progress_checks(
        progress,
        correlations=_complete_correlations(),
        minimum_correlations=30,
        target_health=health,
    )

    assert checks["no_fatal_health_signal"] is False
    assert checks["canonical_chain_progress_complete"] is False


def test_canonical_v2_window_accepts_switch_and_retains_extra_timing(
    monkeypatch, tmp_path
):
    handle = _Handle(tmp_path / "run")
    adopted = [_tip(height) for height in range(101, 111)]
    adopted.append({**_tip(110), "hash": "f" * 64})
    adopted.extend(_tip(height) for height in range(111, 136))
    applications = [
        {"sample_id": f"apply-{index:04d}", "duration_micros": 2.184}
        for index in range(len(adopted) + 1)
    ]
    tips = iter([_tip(100), _tip(135)])
    monkeypatch.setattr(
        primitive_module, "_resolve_client_runtime_target", lambda *_: {"id": "node1"}
    )
    monkeypatch.setattr(
        primitive_module, "_protocol_container_state", lambda *_: _healthy_window()["before"]
    )
    monkeypatch.setattr(
        primitive_module, "_observe_client_target_tip", lambda *_: next(tips)
    )
    monkeypatch.setattr(
        primitive_module,
        "_collect_controlled_block_evidence",
        lambda *_args, **_kwargs: (adopted, applications, []),
    )
    monkeypatch.setattr(
        primitive_module,
        "_collect_raw_chain_events",
        lambda *_args, **_kwargs: [
            {"event": "fork-switch", "fork_length": 2, "rollback_length": 0}
        ],
    )
    monkeypatch.setattr(
        primitive_module,
        "_observe_client_window_health",
        lambda *_args, **_kwargs: _healthy_window(),
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    _primitive(
        "RuntimeControlledChainProgressWindow",
        {
            "progress_contract": "canonical-progress-v2",
            "warm_up_seconds": 0,
            "duration_seconds": 0,
            "minimum_adopted_blocks": 30,
            "max_oscillation_episodes": 3,
            "max_oscillation_transitions": 4,
            "minimum_convergence_blocks": 3,
        },
    ).run(handle, None)

    proof = json.loads(
        (handle.run_dir / "outputs/client-example-proof/controlled-chain-progress.json").read_text()
    )
    assert proof["schema_version"] == "canonical-progress-v2"
    assert proof["checks"]["canonical_chain_progress_complete"] is True
    assert len(proof["raw_evidence"]["application_timings"]) == len(adopted) + 1
    assert len(proof["application_samples"]) == len(adopted)
    assert proof["excluded_application_samples"][-1]["reason"] == (
        "no-adopted-event-temporal-pair"
    )
    assert proof["raw_evidence"]["fork_and_rollback_events"][0]["event"] == (
        "fork-switch"
    )


def test_amaru_raw_chain_events_keep_fork_and_rollback_fields(monkeypatch):
    rows = [
        {
            "timestamp": "2026-09-21T01:00:00Z",
            "fields": {
                "message": "enter",
                "fork_length": 2,
                "rollback_length": 1,
                "fork_point": [100, "a" * 64, 20],
            },
            "span": {"name": "state.switch_to_fork"},
            "id": 7,
        },
        {
            "timestamp": "2026-09-21T01:00:00.1Z",
            "fields": {"message": "enter"},
            "span": {"name": "state.roll_backward"},
            "id": 8,
            "parent_id": 7,
        },
    ]
    monkeypatch.setattr(
        primitive_module,
        "_protocol_docker_result",
        lambda *_args, **_kwargs: type(
            "Result", (), {"stdout": "\n".join(json.dumps(row) for row in rows), "stderr": ""}
        )(),
    )

    events = primitive_module._collect_raw_chain_events(
        {
            "implementation": "amaru",
            "container": "amaru",
            "window_started_at": "2026-09-21T01:00:00Z",
        },
        log_offset=0,
    )

    assert events == [
        {
            "event": "fork-switch",
            "observed_at": "2026-09-21T01:00:00Z",
            "event_id": 7,
            "parent_event_id": None,
            "fork_length": 2,
            "rollback_length": 1,
            "fork_point": [100, "a" * 64, 20],
        },
        {
            "event": "rollback",
            "observed_at": "2026-09-21T01:00:00.1Z",
            "event_id": 8,
            "parent_event_id": 7,
        },
    ]


def test_amaru_controlled_window_prefers_raw_block_apply_nanoseconds(monkeypatch):
    rows = [
        {
            "timestamp": "2026-09-21T03:00:00Z",
            "fields": {
                "message": "tip.update",
                "block_height": 101,
                "header_hash": "a" * 64,
                "slot": 501,
            },
        },
        {
            "timestamp": "2026-09-21T03:00:00.1Z",
            "fields": {
                "message": "measurement.block_apply",
                "point_slot": 501,
                "outcome": "completed",
                "elapsed_micros": 2,
                "elapsed_nanos": 2184,
            },
        },
        {
            "timestamp": "2026-09-21T03:00:00.2Z",
            "fields": {"message": "enter", "point_slot": 501},
            "span": {"name": "block.apply"},
            "id": 9,
        },
        {
            "timestamp": "2026-09-21T03:00:00.3Z",
            "fields": {"message": "exit", "point_slot": 501},
            "span": {"name": "block.apply"},
            "id": 9,
        },
    ]
    monkeypatch.setattr(
        primitive_module,
        "_protocol_docker_result",
        lambda *_args, **_kwargs: type(
            "Result", (), {"stdout": "\n".join(json.dumps(row) for row in rows), "stderr": ""}
        )(),
    )

    adopted, applications, excluded = primitive_module._collect_controlled_block_evidence(
        {
            "implementation": "amaru",
            "container": "amaru",
            "window_started_at": "2026-09-21T03:00:00Z",
        },
        log_offset=0,
        measurement_offset=0,
    )

    assert len(adopted) == 1
    assert excluded == []
    assert applications == [
        {
            "sample_id": "apply-0000",
            "elapsed_nanos": 2184,
            "duration_micros": 2.184,
            "observed_at": "2026-09-21T03:00:00.1Z",
            "point_slot": 501,
            "outcome": "completed",
            "timing_source": "patched-monotonic-nanoseconds",
        }
    ]


def test_amaru_controlled_window_excludes_events_after_end_marker(monkeypatch):
    before_end = {
        "timestamp": "2026-09-21T03:00:00.999999Z",
        "fields": {
            "message": "tip.update",
            "block_height": 101,
            "header_hash": "a" * 64,
            "slot": 501,
        },
    }
    precise_before_end = {
        "timestamp": "2026-09-21T03:00:00.999999Z",
        "fields": {
            "message": "measurement.block_apply",
            "point_slot": 501,
            "outcome": "completed",
            "elapsed_micros": 2,
            "elapsed_nanos": 2184,
        },
    }
    after_end = {
        "timestamp": "2026-09-21T03:00:01.000001Z",
        "fields": {
            "message": "tip.update",
            "block_height": 101,
            "header_hash": "b" * 64,
            "slot": 501,
        },
    }
    precise_after_end = {
        "timestamp": "2026-09-21T03:00:01.000001Z",
        "fields": {
            "message": "measurement.block_apply",
            "point_slot": 502,
            "outcome": "completed",
            "elapsed_micros": 3,
            "elapsed_nanos": 3456,
        },
    }
    fork_after_end = {
        "timestamp": "2026-09-21T03:00:01.000001Z",
        "fields": {"message": "enter", "fork_length": 1},
        "span": {"name": "state.switch_to_fork"},
        "id": 12,
    }
    rows = [
        before_end,
        precise_before_end,
        after_end,
        precise_after_end,
        fork_after_end,
    ]
    monkeypatch.setattr(
        primitive_module,
        "_protocol_docker_result",
        lambda *_args, **_kwargs: type(
            "Result", (), {"stdout": "\n".join(json.dumps(row) for row in rows), "stderr": ""}
        )(),
    )
    target = {
        "implementation": "amaru",
        "container": "amaru",
        "window_started_at": "2026-09-21T03:00:00Z",
        "window_ended_at": "2026-09-21T03:00:01Z",
    }

    adopted, applications, excluded = primitive_module._collect_controlled_block_evidence(
        target,
        log_offset=0,
        measurement_offset=0,
    )
    raw_chain_events = primitive_module._collect_raw_chain_events(target, log_offset=0)

    assert [row["hash"] for row in adopted] == ["a" * 64]
    assert [row["elapsed_nanos"] for row in applications] == [2184]
    assert excluded == []
    assert raw_chain_events == []


def test_amaru_controlled_window_keeps_legacy_span_fallback(monkeypatch):
    rows = [
        {
            "timestamp": "2026-09-21T03:00:00.000001Z",
            "fields": {"message": "enter", "point_slot": 501},
            "span": {"name": "block.apply"},
            "id": 9,
        },
        {
            "timestamp": "2026-09-21T03:00:00.000004Z",
            "fields": {"message": "exit", "point_slot": 501},
            "span": {"name": "block.apply"},
            "id": 9,
        },
    ]
    monkeypatch.setattr(
        primitive_module,
        "_protocol_docker_result",
        lambda *_args, **_kwargs: type(
            "Result", (), {"stdout": "\n".join(json.dumps(row) for row in rows), "stderr": ""}
        )(),
    )

    _adopted, applications, _excluded = primitive_module._collect_controlled_block_evidence(
        {
            "implementation": "amaru",
            "container": "amaru",
            "window_started_at": "2026-09-21T03:00:00Z",
        },
        log_offset=0,
        measurement_offset=0,
    )

    assert applications == [
        {
            "sample_id": "apply-0000",
            "duration_micros": 3.0,
            "observed_at": "2026-09-21T03:00:00.000004Z",
            "point_slot": 501,
            "outcome": "completed",
            "timing_source": "legacy-paired-wall-clock-span",
        }
    ]


def test_canonical_progress_assertion_rederives_the_retained_raw_evidence(tmp_path):
    handle = _Handle(tmp_path / "run")
    proof_dir = handle.run_dir / "outputs/client-example-proof"
    proof_dir.mkdir(parents=True)
    events = [_tip(height) for height in range(101, 111)]
    events.append({**_tip(110), "hash": "f" * 64})
    events.extend(_tip(height) for height in range(111, 136))
    progress = _canonical(events)
    (proof_dir / "controlled-chain-progress.json").write_text(
        json.dumps(
            {
                "schema_version": "canonical-progress-v2",
                "start_tip": _tip(100),
                "end_tip": _tip(135),
                "adopted_blocks": events,
                "canonical_progress": progress,
                "correlations": _complete_correlations(),
                "target_health": _healthy_window(),
            }
        )
    )

    result = _primitive(
        "CanonicalChainProgressComplete",
        {
            "minimum_adopted_blocks": 30,
            "minimum_correlations": 30,
            "max_oscillation_episodes": 3,
            "max_oscillation_transitions": 4,
            "minimum_convergence_blocks": 3,
        },
    ).evaluate(handle)

    assert result["result"] == "pass"
    assert result["evaluated_value"]["canonical_chain_progress_complete"] is True
