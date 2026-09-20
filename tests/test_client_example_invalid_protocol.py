import json
import subprocess
import time
from pathlib import Path

import pytest

from profile_manager import primitives
from profile_manager.scenario import scenario_from_body
from scripts import runtime_amaru_measurement_calibration as amaru_calibration
from scripts import runtime_cardano_measurement_calibration as cardano_calibration


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "dwarf" / "scenarios"
REGISTRY = ROOT / "dwarf" / "primitives" / "registry.json"
EXPECTED_DIGEST = "sha256:1ab6db08d45f22f42b1333c255ed07ac9dc57ecacb69ee7c645ecfd451c4225e"


class _Handle:
    def __init__(self, run_dir, implementation="amaru"):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True)
        self._measurement_context = {
            "resolution": {"target_identity": {"implementation": implementation}}
        }
        self.markers = []
        self.logs = []

        class _Runtime:
            def __init__(runtime_self, outer):
                runtime_self.outer = outer

            def mark_phase(runtime_self, phase_id, state):
                marker = {"phase_id": phase_id, "state": state}
                runtime_self.outer.markers.append(marker)
                return marker

        self._measurement_runtime = _Runtime(self)

    def log(self, **entry):
        self.logs.append(entry)


def _valid_protocol_report():
    attempts = []
    for case, payload, frame in (
        (
            "unsupported-version-refusal",
            "8200a11903e784182af400f4",
            "000000000000000c8200a11903e784182af400f4",
        ),
        ("malformed-cbor", "ff", "0000000000000001ff"),
    ):
        for index in range(100):
            attempts.append(
                {
                    "attempt_id": f"{case}-{index:03d}",
                    "case": case,
                    "payload_hex": payload,
                    "request_hex": frame,
                    "response_hex": "",
                    "started_at": "2026-09-20T00:00:00Z",
                    "completed_at": "2026-09-20T00:00:00.001000Z",
                    "elapsed_micros": 100,
                    "outcome": "rejected",
                    "detail": "eof",
                    "target_endpoint": "127.0.0.1:3000",
                    "listener_reached": True,
                }
            )
    return {
        "status": "available",
        "attempt_records": attempts,
        "target_health": {
            "before": {"running": True, "restart_count": 0, "oom_killed": False},
            "after": {"running": True, "restart_count": 0, "oom_killed": False},
            "tip_before": {"block_height": 10},
            "tip_after": {"block_height": 12},
            "log_signals": {"fatal": [], "background": []},
        },
        "peer_session": {
            "peer_id": "honest-peer-1",
            "before": {"usable": True},
            "during": {"usable": True},
            "after": {"usable": True},
            "recovered_within_seconds": 0.0,
        },
    }


def test_frozen_invalid_case_set_is_exact_for_both_helpers():
    plan = amaru_calibration.build_case_plan(
        attempt_count=200,
        case_set="fixed-handshake-invalid-cases-v1",
    )
    assert [row["name"] for row in plan].count("unsupported-version-refusal") == 100
    assert [row["name"] for row in plan].count("malformed-cbor") == 100
    assert {row["payload_hex"] for row in plan} == {
        "8200a11903e784182af400f4",
        "ff",
    }
    identity = cardano_calibration.build_workload_identity(
        attempt_count=200,
        case_set="fixed-handshake-invalid-cases-v1",
    )
    assert identity["case_set"] == "fixed-handshake-invalid-cases-v1"
    assert {row["name"] for row in identity["cases"]} == {
        "unsupported-version-refusal",
        "malformed-cbor",
    }


def test_amaru_independent_peer_is_the_controlled_consumer():
    runtime = {
        "actual_topology": {"isolated_consumer": "amaru-consumer"},
        "identity": {
            "services": {
                "amaru-consumer": {"container": "dwarf-amaru-consumer-1"}
            }
        },
    }

    assert amaru_calibration._independent_peer(runtime) == (
        "amaru-consumer",
        "dwarf-amaru-consumer-1",
    )


def test_cardano_independent_peer_is_not_the_hostile_target():
    target = {"id": "node1", "container_name": "dwarf-node1"}
    runtime = {
        "nodes": [
            target,
            {
                "id": "node2",
                "container_name": "dwarf-node2",
                "container_socket_path": "/env/socket/node2/sock",
            },
            {"id": "node3", "container_name": "dwarf-node3"},
        ]
    }

    assert cardano_calibration._independent_peer(runtime, target) == (
        "node2",
        "dwarf-node2",
        "/env/socket/node2/sock",
    )


def test_invalid_attempts_are_paced_and_have_complete_timestamps(monkeypatch):
    sleeps = []

    class _Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, _value):
            pass

        def sendall(self, _frame):
            pass

    monkeypatch.setattr(
        amaru_calibration.socket,
        "create_connection",
        lambda *_args, **_kwargs: _Socket(),
    )
    monkeypatch.setattr(amaru_calibration, "_recv_mux_frame", lambda _sock: b"")
    monkeypatch.setattr(amaru_calibration.time, "sleep", sleeps.append)

    records = amaru_calibration.run_attempts(
        host="127.0.0.1",
        port=3000,
        attempt_count=2,
        timeout_seconds=0.1,
        case_set="fixed-handshake-invalid-cases-v1",
        attempt_interval_seconds=0.25,
    )

    assert sleeps == pytest.approx([0.25], abs=0.01)
    assert all(row["started_at"] for row in records)
    assert all(row["completed_at"] for row in records)


def test_protocol_decode_primitive_owns_hostile_window_and_normalizes_result(
    monkeypatch, tmp_path
):
    handle = _Handle(tmp_path / "run")

    observed_commands = []

    def run(command, **_kwargs):
        observed_commands.append(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        report = _valid_protocol_report()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir.joinpath("result.json").write_text(json.dumps(report))
        output_dir.joinpath("attempts.ndjson").write_text(
            "".join(json.dumps(row) + "\n" for row in report["attempt_records"])
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    primitive = primitives.RuntimeProtocolDecodeCases(
        params={
            "runtime_root": "/runtime",
            "attempts_per_case": 100,
            "duration_seconds": 240,
        }
    )

    primitive.run(handle, None)

    assert handle.markers == [
        {"phase_id": "hostile", "state": "start"},
        {"phase_id": "hostile", "state": "end"},
    ]
    result = json.loads(
        (handle.run_dir / "outputs/protocol-decode-cases/result.json").read_text()
    )
    assert result["workload_identity"] == {
        "identity": "fixed-handshake-invalid-cases-v1",
        "seed": "0xBAD0C003",
        "digest": EXPECTED_DIGEST,
        "attempts_per_case": 100,
        "attempt_count": 200,
        "cases": [
            {
                "name": "unsupported-version-refusal",
                "payload_hex": "8200a11903e784182af400f4",
                "mux_frame_hex": "000000000000000c8200a11903e784182af400f4",
                "attempt_count": 100,
            },
            {
                "name": "malformed-cbor",
                "payload_hex": "ff",
                "mux_frame_hex": "0000000000000001ff",
                "attempt_count": 100,
            },
        ],
    }
    assert len(result["attempt_records"]) == 200
    command = observed_commands[0]
    interval = float(command[command.index("--attempt-interval-seconds") + 1])
    assert interval == pytest.approx(240 / 200)


def _write_proofs(run_dir):
    report_path = run_dir / "outputs/protocol-decode-cases/result.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = _valid_protocol_report()
    report["workload_identity"] = {
        "identity": "fixed-handshake-invalid-cases-v1",
        "seed": "0xBAD0C003",
        "digest": EXPECTED_DIGEST,
        "attempts_per_case": 100,
    }
    report_path.write_text(json.dumps(report))
    proof_dir = run_dir / "outputs/client-example-proof"
    proof_dir.mkdir(parents=True, exist_ok=True)
    proof_dir.joinpath("target-health-and-progress.json").write_text(
        json.dumps(
            {
                "checks": {
                    "no_fatal_signals": True,
                    "target_not_oom_killed": True,
                    "target_restart_count_unchanged": True,
                    "target_running_after": True,
                    "target_running_before": True,
                    "target_progressed": True,
                }
            }
        )
    )
    proof_dir.joinpath("peer-session-health.json").write_text(
        json.dumps(
            {
                "peer_id": "honest-peer-1",
                "before": {"usable": True},
                "during": {"usable": True},
                "after": {"usable": True},
                "recovered_within_seconds": 0.0,
            }
        )
    )


@pytest.mark.parametrize(
    "class_name",
    [
        "InvalidProtocolCasesContained",
        "TargetProgressContinues",
        "UnrelatedPeerSessionUsable",
        "NoTargetFatalSignal",
    ],
)
def test_invalid_protocol_assertions_pass_only_on_retained_proof(tmp_path, class_name):
    _write_proofs(tmp_path)
    result = getattr(primitives, class_name)(params={}).evaluate(
        type("Handle", (), {"run_dir": tmp_path})()
    )
    assert result["result"] == "pass"


def test_invalid_protocol_assertion_rejects_missing_completion_timestamp(tmp_path):
    _write_proofs(tmp_path)
    path = tmp_path / "outputs/protocol-decode-cases/result.json"
    report = json.loads(path.read_text())
    report["attempt_records"][0].pop("completed_at")
    path.write_text(json.dumps(report))

    result = primitives.InvalidProtocolCasesContained(params={}).evaluate(
        type("Handle", (), {"run_dir": tmp_path})()
    )

    assert result["result"] == "fail"
    assert "unsupported-version-refusal:identity" in result["evaluated_value"]["failures"]


def test_invalid_protocol_assertion_rejects_missing_listener_endpoint(tmp_path):
    _write_proofs(tmp_path)
    path = tmp_path / "outputs/protocol-decode-cases/result.json"
    report = json.loads(path.read_text())
    report["attempt_records"][0]["target_endpoint"] = ""
    path.write_text(json.dumps(report))

    result = primitives.InvalidProtocolCasesContained(params={}).evaluate(
        type("Handle", (), {"run_dir": tmp_path})()
    )

    assert result["result"] == "fail"
    assert "unsupported-version-refusal:endpoint" in result["evaluated_value"]["failures"]


def test_invalid_protocol_scenarios_match_frozen_cards_and_reference_all_controls():
    expected = {
        "amaru": ("profile-q-amaru-measurement-patched", "amaru-security-patched"),
        "cardano": ("profile-t-cardano-measurement-patched", "cardano-security-patched"),
    }
    for suffix, (profile, measurement_profile) in expected.items():
        path = SCENARIOS / f"client-example-invalid-mini-protocol-{suffix}.yaml"
        body = json.loads(path.read_text())
        parsed = scenario_from_body((json.dumps(body) + "\n").encode())
        assert parsed.profile == profile
        assert parsed.measurement_profile == measurement_profile
        assert body["seed"] == "0xBAD0C003"
        assert [row["primitive"] for row in body["setup"]] == [
            "runtime_verify_exact_target",
            "runtime_mark_baseline_window",
        ]
        assert [row["primitive"] for row in body["load"]] == [
            "runtime_protocol_decode_cases",
            "runtime_mark_hostile_window",
            "runtime_mark_recovery_window",
        ]
        workload = body["load"][0]
        assert workload["attempts_per_case"] == 100
        assert workload["duration_seconds"] == 240
        assert workload["workload_digest"] == EXPECTED_DIGEST
        assert [row["primitive"] for row in body["probes"]] == [
            "runtime_target_health_and_progress",
            "runtime_peer_session_health",
        ]
        assert [row["primitive"] for row in body["assertions"]] == [
            "invalid_protocol_cases_contained",
            "target_progress_continues",
            "unrelated_peer_session_usable",
            "no_target_fatal_signal",
        ]


def test_invalid_protocol_primitives_are_registered_with_schemas():
    registry = primitives.load_registry(REGISTRY)
    expected = {
        "runtime_protocol_decode_cases": "load",
        "invalid_protocol_cases_contained": "assertion",
        "target_progress_continues": "assertion",
        "unrelated_peer_session_usable": "assertion",
        "no_target_fatal_signal": "assertion",
    }
    for name, family in expected.items():
        assert registry[name].family == family
        assert (ROOT / "dwarf" / registry[name].params_schema).is_file()
