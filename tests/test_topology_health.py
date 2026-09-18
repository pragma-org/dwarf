import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dwarf"))

from profile_manager.topology_health import classify_topology_health  # noqa: E402


PROBE = ROOT / "dwarf" / "scripts" / "check_cardano_amaru_topology.py"
REDEPLOY = ROOT / "dwarf" / "scripts" / "redeploy_cardano_amaru_topology.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("check_cardano_amaru_topology", PROBE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_redeploy():
    spec = importlib.util.spec_from_file_location(
        "redeploy_cardano_amaru_topology", REDEPLOY
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


REQUIRED = (
    "p1",
    "p2",
    "p3",
    "relay1",
    "relay2",
    "amaru-relay-1",
    "amaru-relay-2",
    "amaru-consumer",
)


def _containers():
    return {
        name: {
            "present": True,
            "running": True,
            "restart_count": 0,
            "oom_killed": False,
            "image": f"example/{name}:test",
            "image_digest": f"sha256:{name}",
            "fatal_signatures": [],
        }
        for name in REQUIRED
    }


def _tip(slot, block=None, hash_value=None):
    return {
        "slot": slot,
        "block": slot if block is None else block,
        "hash": hash_value or f"hash-{slot}",
    }


def _observation(*, consumer=(1000, 1002), relay=(1000, 1002), reference=(1000, 1002)):
    samples = []
    for idx in range(2):
        ref_slot = reference[idx]
        consumer_slot = consumer[idx]
        relay_slot = relay[idx]
        tips = {
            name: _tip(ref_slot, hash_value=f"chain-{ref_slot}")
            for name in ("p1", "p2", "p3", "relay1", "relay2")
        }
        tips["amaru-consumer"] = _tip(
            consumer_slot,
            hash_value=f"chain-{consumer_slot}",
        )
        samples.append(
            {
                "at": f"2026-09-15T00:00:0{idx}Z",
                "tips": tips,
                "amaru_relays": {
                    "amaru-relay-1": {
                        "current_slot": relay_slot,
                        "highest_slot": ref_slot,
                        "fatal_signatures": [],
                    },
                    "amaru-relay-2": {
                        "current_slot": relay_slot,
                        "highest_slot": ref_slot,
                        "fatal_signatures": [],
                    },
                },
            }
        )
    return {
        "schema_version": 1,
        "topology_id": "cardano_amaru",
        "required_services": list(REQUIRED),
        "containers": _containers(),
        "peer_contract": {"amaru_consumer_only_amaru_upstreams": True},
        "samples": samples,
        "tolerances": {"slot_lag": 6, "producer_slot_spread": 6},
    }


def test_healthy_mixed_topology_requires_all_targets_current():
    result = classify_topology_health(_observation())

    assert result["state"] == "healthy"
    assert result["reason_code"] == "all_mixed_readiness_gates_passed"
    assert result["reference_tip"]["slot"] == 1002
    assert result["consumer_lag_slots"] == 0


def test_missing_required_container_is_unhealthy():
    observation = _observation()
    observation["containers"]["amaru-relay-2"]["present"] = False

    result = classify_topology_health(observation)

    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "required_container_missing"
    assert result["affected"] == ["amaru-relay-2"]


def test_restarted_or_oom_container_is_unhealthy():
    restarted = _observation()
    restarted["containers"]["amaru-relay-1"]["restart_count"] = 1
    assert classify_topology_health(restarted)["reason_code"] == "container_restarted"

    oom = _observation()
    oom["containers"]["amaru-relay-1"]["oom_killed"] = True
    assert classify_topology_health(oom)["reason_code"] == "container_oom_killed"


def test_cardano_producer_divergence_is_unhealthy():
    observation = _observation()
    observation["samples"][-1]["tips"]["p3"] = _tip(950, hash_value="fork")

    result = classify_topology_health(observation)

    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "cardano_reference_divergent"


def test_stalled_amaru_consumer_is_unhealthy():
    result = classify_topology_health(
        _observation(consumer=(100, 100), relay=(1000, 1002))
    )

    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "amaru_consumer_stalled"
    assert result["consumer_lag_slots"] == 902


def test_stalled_amaru_relay_is_unhealthy():
    result = classify_topology_health(
        _observation(consumer=(1000, 1002), relay=(100, 100))
    )

    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "amaru_relay_stalled"
    assert result["affected"] == ["amaru-relay-1", "amaru-relay-2"]


def test_relay_failure_still_reports_consumer_lag():
    result = classify_topology_health(
        _observation(consumer=(100, 100), relay=(100, 100))
    )

    assert result["reason_code"] == "amaru_relay_stalled"
    assert result["consumer_lag_slots"] == 902


def test_advancing_but_lagging_mixed_topology_is_catching_up():
    result = classify_topology_health(
        _observation(consumer=(100, 120), relay=(100, 120))
    )

    assert result["state"] == "catching_up"
    assert result["reason_code"] == "mixed_topology_catching_up"


def test_fatal_amaru_signature_is_unhealthy():
    observation = _observation()
    observation["samples"][-1]["amaru_relays"]["amaru-relay-1"][
        "fatal_signatures"
    ] = ["panic"]

    result = classify_topology_health(observation)

    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "amaru_fatal_signal"


def test_invalid_consumer_peer_contract_is_unhealthy():
    observation = _observation()
    observation["peer_contract"]["amaru_consumer_only_amaru_upstreams"] = False

    result = classify_topology_health(observation)

    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "consumer_peer_contract_invalid"


def test_missing_relay_visibility_is_unknown_not_healthy():
    observation = _observation()
    del observation["samples"][-1]["amaru_relays"]["amaru-relay-2"]

    result = classify_topology_health(observation)

    assert result["state"] == "unknown"
    assert result["reason_code"] == "insufficient_amaru_visibility"


def test_one_sample_is_unknown_not_healthy():
    observation = _observation()
    observation["samples"] = observation["samples"][:1]

    result = classify_topology_health(observation)

    assert result["state"] == "unknown"
    assert result["reason_code"] == "insufficient_samples"


def test_result_is_json_serializable():
    json.dumps(classify_topology_health(_observation()))


def test_probe_parses_current_and_highest_amaru_points():
    probe = _load_probe()
    text = """
2026-09-15T04:27:42Z INFO chainsync.intersect_found current="[13920, h'aaa', 2829]" highest="[169920, h'bbb', 33979]"
2026-09-15T04:27:43Z INFO chainsync.roll_backward current="[13923, h'ccc', 2831]" highest="[169926, h'ddd', 33980]"
"""

    parsed = probe.parse_amaru_progress(text)

    assert parsed["current_slot"] == 13923
    assert parsed["current_block"] == 2831
    assert parsed["highest_slot"] == 169926
    assert parsed["fatal_signatures"] == []


def test_probe_prefers_latest_adopted_tip_over_sparse_chainsync_current():
    probe = _load_probe()
    text = """
2026-09-15T06:08:24Z INFO chainsync.roll_backward current="[1000, h'aaa', 200]" highest="[1200, h'bbb', 240]"
2026-09-15T06:09:13Z INFO amaru::consensus: tip.adopt slot=1210 header_hash="ccc" block_height=242 max_block_height=242
"""

    parsed = probe.parse_amaru_progress(text)

    assert parsed["current_slot"] == 1210
    assert parsed["current_hash"] == "ccc"
    assert parsed["current_block"] == 242
    assert parsed["highest_slot"] == 1200


def test_probe_parses_20260730_adopted_tip_rendering():
    probe = _load_probe()
    text = """
2026-09-18T04:51:47Z INFO amaru_consensus::stages::track_peers: intersect found peer=relay1.example:3001 current=1182.aaa highest=1369.bbb
2026-09-18T04:53:45Z INFO amaru_consensus::stages::adopt_chain: adopted tip tip.slot=1612 tip.hash=ccc tip.block_height=316 max_block_height=316 suppressed=0
"""

    parsed = probe.parse_amaru_progress(text)

    assert parsed["current_slot"] == 1612
    assert parsed["current_hash"] == "ccc"
    assert parsed["current_block"] == 316
    assert parsed["highest_slot"] == 1369


def test_probe_parses_live_amaru_json_progress_rendering():
    probe = _load_probe()
    text = """
{"timestamp":"2026-09-18T20:20:17.411630Z","level":"INFO","fields":{"current":[1199,"f11f",213],"highest":[1297,"a1f1",236],"message":"chainsync.intersect_found"},"target":"amaru::consensus"}
{"timestamp":"2026-09-18T20:22:55.507756Z","level":"INFO","fields":{"block_height":309,"header_hash":"a7ad","max_block_height":309,"message":"tip.adopt","slot":1615},"target":"amaru::consensus"}
"""

    parsed = probe.parse_amaru_progress(text)

    assert parsed["current_slot"] == 1615
    assert parsed["current_hash"] == "a7ad"
    assert parsed["current_block"] == 309
    assert parsed["highest_slot"] == 1297
    assert parsed["highest_hash"] == "a1f1"
    assert parsed["highest_block"] == 236


def test_probe_retains_bootstrap_targets_for_fresh_readiness_proof():
    probe = _load_probe()
    text = """
[bootstrap] snapshot_slots=399 799 1199
[bootstrap] committed bundle to /srv/amaru
[bootstrap] exec'ing amaru run
INFO chainsync.intersect_found current="[1601, h'aaa', 300]" highest="[1601, h'aaa', 300]"
"""

    parsed = probe.parse_amaru_progress(text)

    assert parsed["target_slots"] == [399, 799, 1199]
    assert parsed["committed"] is True
    assert parsed["exec_started"] is True


def test_probe_detects_existing_amaru_fatal_signatures():
    probe = _load_probe()

    parsed = probe.parse_amaru_progress("thread panicked at listener EADDRINUSE")

    assert "panic" in parsed["fatal_signatures"]
    assert "listener-address-in-use" in parsed["fatal_signatures"]


def test_probe_peer_contract_accepts_only_the_two_amaru_relays():
    probe = _load_probe()
    body = {
        "localRoots": [
            {
                "accessPoints": [
                    {"address": "amaru-relay-1.example", "port": 3000},
                    {"address": "amaru-relay-2.example", "port": 3000},
                ]
            }
        ],
        "publicRoots": [],
    }

    assert probe.consumer_uses_only_amaru(json.dumps(body)) is True

    body["localRoots"][0]["accessPoints"].append(
        {"address": "p1.example", "port": 3001}
    )
    assert probe.consumer_uses_only_amaru(json.dumps(body)) is False


def test_probe_collects_two_samples_and_writes_classified_evidence(tmp_path):
    probe = _load_probe()
    calls = {"sample": 0}

    containers = {
        name: {
            "present": True,
            "running": True,
            "restart_count": 0,
            "oom_killed": False,
            "image": f"example/{name}:test",
            "image_digest": f"sha256:{name}",
            "fatal_signatures": [],
            "container_name": f"dwarf-control-{name}",
        }
        for name in REQUIRED
    }

    def container_reader(_project):
        return containers

    def sample_reader(_containers):
        idx = calls["sample"]
        calls["sample"] += 1
        slot = 1000 + idx * 2
        tips = {
            name: _tip(slot, hash_value=f"chain-{slot}")
            for name in (*CARDANO_REFERENCE_NODES_FOR_TEST, "amaru-consumer")
        }
        return {
            "at": f"2026-09-15T00:00:0{idx}Z",
            "tips": tips,
            "amaru_relays": {
                name: {
                    "current_slot": slot,
                    "highest_slot": slot,
                    "fatal_signatures": [],
                }
                for name in ("amaru-relay-1", "amaru-relay-2")
            },
        }

    output = tmp_path / "topology-health.json"
    result = probe.collect_and_classify(
        project="cardano_amaru_relay_bootstrap_control",
        output=output,
        sample_seconds=0,
        container_reader=container_reader,
        sample_reader=sample_reader,
        peer_contract_reader=lambda _containers: True,
        sleeper=lambda _seconds: None,
    )

    assert calls["sample"] == 2
    assert result["state"] == "healthy"
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["state"] == "healthy"
    assert saved["observation"]["containers"]["p1"]["image_digest"] == "sha256:p1"


def test_probe_fails_if_container_restarts_between_samples(tmp_path):
    probe = _load_probe()
    reads = {"containers": 0, "sample": 0}

    def container_reader(_project):
        reads["containers"] += 1
        containers = _containers()
        for name, value in containers.items():
            value["container_name"] = f"dwarf-control-{name}"
            value["started_at"] = "2026-09-18T00:00:00Z"
        if reads["containers"] > 1:
            containers["amaru-consumer"]["started_at"] = "2026-09-18T00:00:05Z"
        return containers

    def sample_reader(_containers):
        reads["sample"] += 1
        slot = 1000 + reads["sample"]
        tips = {
            name: _tip(slot, hash_value=f"chain-{slot}")
            for name in (*CARDANO_REFERENCE_NODES_FOR_TEST, "amaru-consumer")
        }
        return {
            "tips": tips,
            "amaru_relays": {
                name: {"current_slot": slot, "fatal_signatures": []}
                for name in ("amaru-relay-1", "amaru-relay-2")
            },
        }

    result = probe.collect_and_classify(
        project="cardano_amaru_relay_bootstrap_control",
        output=tmp_path / "topology-health.json",
        sample_seconds=0,
        container_reader=container_reader,
        sample_reader=sample_reader,
        peer_contract_reader=lambda _containers: True,
        sleeper=lambda _seconds: None,
    )

    assert reads["containers"] == 2
    assert result["state"] == "unhealthy"
    assert result["reason_code"] == "container_restarted"
    assert result["affected"] == ["amaru-consumer"]


def test_require_healthy_exit_code_fails_closed():
    probe = _load_probe()

    assert probe.result_exit_code({"state": "healthy"}, require_healthy=True) == 0
    assert probe.result_exit_code({"state": "unhealthy"}, require_healthy=True) == 2
    assert probe.result_exit_code({"state": "unknown"}, require_healthy=True) == 2
    assert probe.result_exit_code({"state": "unhealthy"}, require_healthy=False) == 0


CARDANO_REFERENCE_NODES_FOR_TEST = ("p1", "p2", "p3", "relay1", "relay2")


def _fresh_health(state="healthy"):
    slot = 1601
    containers = _containers()
    for name, value in containers.items():
        value["container_name"] = f"dwarf-control-{name}"
    tips = {
        name: _tip(slot, hash_value="fresh-chain")
        for name in (*CARDANO_REFERENCE_NODES_FOR_TEST, "amaru-consumer")
    }
    relays = {
        name: {
            "target_slots": [399, 799, 1199],
            "committed": True,
            "exec_started": True,
            "current_slot": slot,
            "highest_slot": slot,
            "fatal_signatures": [],
        }
        for name in ("amaru-relay-1", "amaru-relay-2")
    }
    return {
        "state": state,
        "reason_code": "all_mixed_readiness_gates_passed",
        "consumer_lag_slots": 0,
        "observation": {
            "containers": containers,
            "samples": [
                {"tips": tips, "amaru_relays": relays},
                {"tips": tips, "amaru_relays": relays},
            ],
        },
    }


def test_redeploy_fresh_readiness_requires_post_bootstrap_relay_progress():
    redeploy = _load_redeploy()
    result = _fresh_health()

    assert redeploy.fresh_readiness_proven(result) is True

    result["observation"]["samples"][-1]["amaru_relays"]["amaru-relay-2"][
        "current_slot"
    ] = 1200
    assert redeploy.fresh_readiness_proven(result) is False


def test_redeploy_retains_early_bootstrap_proof_when_logs_roll_forward():
    redeploy = _load_redeploy()
    early = _fresh_health(state="catching_up")
    evidence = redeploy.remember_bootstrap_evidence(early, {})
    later = _fresh_health()
    for relay in later["observation"]["samples"][-1]["amaru_relays"].values():
        relay["target_slots"] = []
        relay["committed"] = False
        relay["exec_started"] = False

    assert redeploy.fresh_readiness_proven(later, evidence) is True


def test_redeploy_preserves_evidence_before_exact_project_down(tmp_path):
    redeploy = _load_redeploy()
    package = tmp_path / "cardano_amaru_relay_bootstrap_control"
    package.mkdir()
    (package / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    calls = []
    command_timeouts = []
    capture_was_complete_at_down = []

    rendered = {
        "services": {name: {} for name in REQUIRED},
        "volumes": {"state": {}},
    }

    def command_runner(args, *, timeout=30):
        calls.append(list(args))
        command_timeouts.append((list(args), timeout))
        if args[-3:] == ["config", "--format", "json"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(rendered), "")
        if args[-2:] == ["down", "-v"]:
            evidence_root = (
                tmp_path
                / "state"
                / "topology-health"
                / "redeployments"
                / "20260915T000000Z"
            )
            capture_was_complete_at_down.append(
                (evidence_root / "capture-complete.json").is_file()
            )
        return subprocess.CompletedProcess(args, 0, "ok\n", "")

    health_results = [
        {"state": "unhealthy", "reason_code": "amaru_relay_stalled"},
        {"state": "catching_up", "reason_code": "mixed_topology_catching_up"},
        _fresh_health(),
    ]

    def health_probe(*, project, output, sample_seconds):
        result = health_results.pop(0)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result), encoding="utf-8")
        return result

    result = redeploy.redeploy_topology(
        package=package,
        state_dir=tmp_path / "state",
        timeout_seconds=901,
        sample_seconds=0,
        command_runner=command_runner,
        health_probe=health_probe,
        sleeper=lambda _seconds: None,
        timestamp=lambda: "20260915T000000Z",
    )

    down = [
        "docker",
        "compose",
        "-p",
        redeploy.PROJECT,
        "-f",
        str(package / "docker-compose.yaml"),
        "down",
        "-v",
    ]
    up = [
        "docker",
        "compose",
        "-p",
        redeploy.PROJECT,
        "-f",
        str(package / "docker-compose.yaml"),
        "up",
        "-d",
    ]
    assert down in calls
    assert up in calls
    assert calls.index(down) < calls.index(up)
    assert dict((tuple(command), timeout) for command, timeout in command_timeouts)[
        tuple(up)
    ] >= 901
    assert capture_was_complete_at_down == [True]
    assert result["passed"] is True
    assert result["topology_left_running"] is True


def test_redeploy_rejects_external_compose_volumes(tmp_path):
    redeploy = _load_redeploy()
    package = tmp_path / "cardano_amaru_relay_bootstrap_control"
    package.mkdir()
    (package / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")

    def command_runner(args, *, timeout=30):
        rendered = {
            "services": {name: {} for name in REQUIRED},
            "volumes": {"unsafe": {"external": True}},
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(rendered), "")

    with pytest.raises(RuntimeError, match="external volume"):
        redeploy.redeploy_topology(
            package=package,
            state_dir=tmp_path / "state",
            timeout_seconds=1,
            command_runner=command_runner,
            health_probe=lambda **_kwargs: {},
            sleeper=lambda _seconds: None,
            timestamp=lambda: "20260915T000000Z",
        )


def test_redeploy_never_tears_down_when_evidence_capture_fails(tmp_path):
    redeploy = _load_redeploy()
    package = tmp_path / "cardano_amaru_relay_bootstrap_control"
    package.mkdir()
    (package / "docker-compose.yaml").write_text("services: {}\n")
    calls = []
    rendered = {"services": {name: {} for name in REQUIRED}, "volumes": {}}

    def command_runner(args, *, timeout=30):
        calls.append(list(args))
        if args[-3:] == ["config", "--format", "json"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(rendered), "")
        if "logs" in args:
            return subprocess.CompletedProcess(args, 1, "", "capture failed")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    with pytest.raises(RuntimeError, match="evidence capture failed"):
        redeploy.redeploy_topology(
            package=package,
            state_dir=tmp_path / "state",
            sample_seconds=0,
            command_runner=command_runner,
            health_probe=lambda **_kwargs: {
                "state": "unhealthy",
                "reason_code": "amaru_relay_stalled",
            },
            timestamp=lambda: "20260915T000000Z",
        )

    assert not any("down" in call or "up" in call for call in calls)


def test_redeploy_does_not_start_after_failed_teardown(tmp_path):
    redeploy = _load_redeploy()
    package = tmp_path / "cardano_amaru_relay_bootstrap_control"
    package.mkdir()
    (package / "docker-compose.yaml").write_text("services: {}\n")
    calls = []
    rendered = {"services": {name: {} for name in REQUIRED}, "volumes": {}}

    def command_runner(args, *, timeout=30):
        calls.append(list(args))
        if args[-3:] == ["config", "--format", "json"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(rendered), "")
        if args[-2:] == ["down", "-v"]:
            return subprocess.CompletedProcess(args, 1, "", "down failed")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    with pytest.raises(RuntimeError, match="topology teardown failed"):
        redeploy.redeploy_topology(
            package=package,
            state_dir=tmp_path / "state",
            sample_seconds=0,
            command_runner=command_runner,
            health_probe=lambda **_kwargs: {
                "state": "unhealthy",
                "reason_code": "amaru_relay_stalled",
            },
            timestamp=lambda: "20260915T000000Z",
        )

    assert not any(call[-2:] == ["up", "-d"] for call in calls)


def test_redeploy_readiness_timeout_never_retries_the_mutation(tmp_path):
    redeploy = _load_redeploy()
    package = tmp_path / "cardano_amaru_relay_bootstrap_control"
    package.mkdir()
    (package / "docker-compose.yaml").write_text("services: {}\n")
    calls = []
    rendered = {"services": {name: {} for name in REQUIRED}, "volumes": {}}
    clock = iter([0.0, 0.0, 2.0])

    def command_runner(args, *, timeout=30):
        calls.append(list(args))
        if args[-3:] == ["config", "--format", "json"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(rendered), "")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    result = redeploy.redeploy_topology(
        package=package,
        state_dir=tmp_path / "state",
        timeout_seconds=1,
        sample_seconds=0,
        command_runner=command_runner,
        health_probe=lambda **_kwargs: {
            "state": "unhealthy",
            "reason_code": "amaru_fatal_signal",
        },
        sleeper=lambda _seconds: None,
        monotonic=lambda: next(clock),
        timestamp=lambda: "20260915T000000Z",
    )

    assert result["passed"] is False
    assert result["topology_left_running"] is True
    assert sum(call[-2:] == ["down", "-v"] for call in calls) == 1
    assert sum(call[-2:] == ["up", "-d"] for call in calls) == 1


def test_redeploy_refuses_lock_held_by_attached_scenario(tmp_path):
    redeploy = _load_redeploy()
    from profile_manager.topology_health import acquire_topology_lock

    state_dir = tmp_path / "state"
    held = acquire_topology_lock(state_dir, exclusive=False)
    try:
        with pytest.raises(redeploy.TopologyBusyError):
            redeploy.acquire_redeploy_lock(state_dir)
    finally:
        held.close()
