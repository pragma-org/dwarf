import json
import subprocess
from pathlib import Path

from profile_manager.primitives import (
    AmaruMeasurementBoundaryProven,
    RuntimeAmaruMeasurementCalibration,
)
from profile_manager.scenario import scenario_from_body


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "dwarf" / "scenarios"
PROFILES = ROOT / "dwarf" / "profiles"


def _document(name):
    return json.loads((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))


def test_calibration_profiles_are_additive_exact_and_use_distinct_runtime_identities():
    stock = json.loads(
        (PROFILES / "profile-r-amaru-measurement-stock-control" / "profile.yaml").read_text()
    )
    patched = json.loads(
        (PROFILES / "profile-q-amaru-measurement-patched" / "profile.yaml").read_text()
    )

    assert stock["version_policy"] == patched["version_policy"] == "exact"
    assert stock["amaru_version"] == patched["amaru_version"] == "10.11.20260912"
    assert stock["measurement_target_mode"] == "stock"
    assert patched["measurement_target_mode"] == "patched"
    assert stock["amaru_json_traces"] is True
    assert patched["amaru_json_traces"] is True
    assert patched["measurement_patch_revision"] == "b159172f25a9c389f82f20bca4f15e3032791638"
    assert len(patched["measurement_patch_set_sha256"]) == 64
    assert stock["id"] != patched["id"]


def test_calibration_scenarios_parse_and_have_identical_fixed_workloads():
    stock = _document("amaru-measurement-overhead-calibration-stock")
    patched = _document("amaru-measurement-overhead-calibration-patched")

    for document in (stock, patched):
        parsed = scenario_from_body((json.dumps(document) + "\n").encode())
        assert parsed.seed == "0xA11CE501"
        assert parsed.runtime == "devnet"

    assert stock["seed"] == patched["seed"]
    stock_load = {**stock["load"][0], "runtime_root": "<paired>"}
    patched_load = {**patched["load"][0], "runtime_root": "<paired>"}
    assert stock_load == patched_load
    assert stock_load["primitive"] == "runtime_amaru_measurement_calibration"
    assert stock_load["attempts"] == 40
    assert stock["profile"] != patched["profile"]
    assert stock["measurement_profile"] == "amaru-security-default"
    assert patched["measurement_profile"] == "amaru-security-patched"
    assert stock["assertions"] == patched["assertions"]


def test_calibration_helper_default_is_installed_source_relative():
    helper = Path(RuntimeAmaruMeasurementCalibration._DEFAULT_HELPER)

    assert helper == ROOT / "dwarf" / "scripts" / "runtime_amaru_measurement_calibration.py"
    assert helper.is_file()


def test_patched_boundary_proof_and_stock_control_are_additive_and_identical():
    patched = _document("amaru-measurement-boundary-proof-patched")
    stock = _document("amaru-measurement-boundary-control-stock")

    for document, profile, measurement_profile in (
        (
            patched,
            "profile-q-amaru-measurement-patched",
            "amaru-security-patched",
        ),
        (
            stock,
            "profile-r-amaru-measurement-stock-control",
            "amaru-security-default",
        ),
    ):
        parsed = scenario_from_body((json.dumps(document) + "\n").encode())
        assert parsed.profile == profile
        assert parsed.measurement_profile == measurement_profile
        assert document["seed"] == "0xA11CE502"
        assert document["load"] == [
            {
                "primitive": "runtime_amaru_measurement_calibration",
                "profile_id": profile,
                "attempts": 120,
                "case_set": "accepted-and-rejected-v1",
                "response_timeout_seconds": 2,
                "progress_timeout_seconds": 120,
                "timeout_seconds": 300,
                "expected_helper_exit": 0,
            }
        ]
        assert document["assertions"] == [
            {
                "primitive": "amaru_measurement_boundary_proven",
                "expected_mode": "patched" if "patched" in profile else "stock",
                "min_attempts_per_case": 40,
                "min_internal_samples_per_outcome": 30 if "patched" in profile else 0,
            }
        ]

    patched_load = {**patched["load"][0], "profile_id": "<paired>"}
    stock_load = {**stock["load"][0], "profile_id": "<paired>"}
    assert patched_load == stock_load


def _boundary_result(
    tmp_path, *, malformed_samples=40, progressed=True, include_handshake_outcomes=True
):
    path = tmp_path / "outputs" / "amaru-measurement-calibration" / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "status": "available",
                "target": {"mode": "patched", "source_revision": "b159172f"},
                "workload_identity": {
                    "case_set": "accepted-and-rejected-v1",
                    "workload_digest": "sha256:" + "a" * 64,
                },
                "attempts": {
                    "total": 120,
                    "unexpected_count": 0,
                    "by_case": {
                        "supported-version-acceptance": {
                            "total": 40,
                            "outcomes": {"accepted": 40},
                        },
                        "unsupported-version-refusal": {
                            "total": 40,
                            "outcomes": {"rejected": 40},
                        },
                        "malformed-cbor-rejection": {
                            "total": 40,
                            "outcomes": {"rejected": 40},
                        },
                    },
                },
                "target_health": {
                    "checks": {
                        "all_attempts_classified_as_expected": True,
                        "target_running_before": True,
                        "target_running_after": True,
                        "target_not_oom_killed": True,
                        "target_restart_count_unchanged": True,
                        "honest_chain_progressed": progressed,
                        "no_fatal_signals": True,
                    }
                },
                "node_measurements": {
                    "amaru-patched-protocol-decode": {
                        "export": {"incomplete": False},
                        "measurements": {
                            "handshake_ingress_by_outcome": {
                                "framed": {"sample_count": 80},
                                "malformed": {"sample_count": malformed_samples},
                            },
                            "handshake_decode_by_decode_outcome": {
                                "decoded": {"sample_count": 80},
                            },
                            "handshake_state_by_outcome": (
                                {
                                    "accepted": {"sample_count": 80},
                                }
                                if include_handshake_outcomes
                                else {}
                            ),
                            "handshake_negotiation_by_outcome": (
                                {
                                    "accepted": {"sample_count": 40},
                                    "refused": {"sample_count": 40},
                                }
                                if include_handshake_outcomes
                                else {}
                            ),
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _rewrite_decode_boundary(tmp_path, *, ingress_malformed, decode_malformed):
    """Reshape the fixture the way 10.11.20260918 reports malformed CBOR."""
    path = tmp_path / "outputs" / "amaru-measurement-calibration" / "result.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    measurements = body["node_measurements"]["amaru-patched-protocol-decode"]["measurements"]
    measurements["handshake_ingress_by_outcome"] = {
        "framed": {"sample_count": 80 + decode_malformed},
        **({"malformed": {"sample_count": ingress_malformed}} if ingress_malformed else {}),
    }
    measurements["handshake_decode_by_decode_outcome"]["malformed"] = {
        "sample_count": decode_malformed
    }
    path.write_text(json.dumps(body), encoding="utf-8")


def _evaluate_boundary(tmp_path):
    handle = type("Handle", (), {"run_dir": tmp_path})()
    return AmaruMeasurementBoundaryProven(
        params={
            "expected_mode": "patched",
            "min_attempts_per_case": 40,
            "min_internal_samples_per_outcome": 30,
        }
    ).evaluate(handle)


def test_boundary_assertion_accepts_malformed_rejected_at_the_decode_boundary(tmp_path):
    _boundary_result(tmp_path)
    _rewrite_decode_boundary(tmp_path, ingress_malformed=0, decode_malformed=40)

    result = _evaluate_boundary(tmp_path)

    assert result["result"] == "pass"
    assert result["evaluated_value"]["internal_malformed_samples"] == 40
    assert result["evaluated_value"]["internal_malformed_boundary"] == "mini-protocol-decode"
    assert result["evaluated_value"]["internal_framed_samples"] == 120


def test_boundary_assertion_keeps_ingress_boundary_for_older_revisions(tmp_path):
    _boundary_result(tmp_path)

    result = _evaluate_boundary(tmp_path)

    assert result["result"] == "pass"
    assert result["evaluated_value"]["internal_malformed_boundary"] == "mux-cbor-item"


def test_boundary_assertion_refuses_malformed_counted_at_both_boundaries(tmp_path):
    _boundary_result(tmp_path)
    _rewrite_decode_boundary(tmp_path, ingress_malformed=40, decode_malformed=40)

    result = _evaluate_boundary(tmp_path)

    assert result["result"] == "fail"
    assert result["evaluated_value"]["internal_malformed_samples"] == 80


def test_boundary_assertion_requires_external_health_and_internal_outcome_samples(tmp_path):
    _boundary_result(tmp_path)
    handle = type("Handle", (), {"run_dir": tmp_path})()

    result = AmaruMeasurementBoundaryProven(
        params={
            "expected_mode": "patched",
            "min_attempts_per_case": 40,
            "min_internal_samples_per_outcome": 30,
        }
    ).evaluate(handle)

    assert result["result"] == "pass"
    assert result["evaluated_value"]["external_attempts"] == 120
    assert result["evaluated_value"]["internal_decoded_samples"] == 80
    assert result["evaluated_value"]["internal_malformed_samples"] == 40
    assert result["evaluated_value"]["internal_negotiation_accepted_samples"] == 40
    assert result["evaluated_value"]["internal_negotiation_refused_samples"] == 40


def test_boundary_assertion_fails_when_internal_state_outcomes_are_absent(tmp_path):
    _boundary_result(tmp_path, include_handshake_outcomes=False)
    handle = type("Handle", (), {"run_dir": tmp_path})()

    result = AmaruMeasurementBoundaryProven(
        params={
            "expected_mode": "patched",
            "min_attempts_per_case": 40,
            "min_internal_samples_per_outcome": 30,
        }
    ).evaluate(handle)

    assert result["result"] == "fail"
    assert result["evaluated_value"]["internal_state_admitted_samples"] == 0
    assert result["evaluated_value"]["internal_negotiation_accepted_samples"] == 0
    assert result["evaluated_value"]["internal_negotiation_refused_samples"] == 0


def test_boundary_assertion_does_not_accept_unrelated_generic_protocol_samples(tmp_path):
    _boundary_result(tmp_path, include_handshake_outcomes=False)
    report = (
        tmp_path / "outputs" / "amaru-measurement-calibration" / "result.json"
    )
    body = json.loads(report.read_text(encoding="utf-8"))
    body["node_measurements"]["amaru-patched-protocol-decode"]["measurements"].update(
        {
            "protocol_decode_by_decode_outcome": {
                "decoded": {"sample_count": 1000},
                "malformed": {"sample_count": 1000},
            },
            "protocol_total_by_state_outcome": {
                "accepted": {"sample_count": 1000},
                "rejected": {"sample_count": 1000},
                "not_attempted": {"sample_count": 1000},
            },
        }
    )
    report.write_text(json.dumps(body), encoding="utf-8")
    handle = type("Handle", (), {"run_dir": tmp_path})()

    result = AmaruMeasurementBoundaryProven(
        params={
            "expected_mode": "patched",
            "min_attempts_per_case": 40,
            "min_internal_samples_per_outcome": 30,
        }
    ).evaluate(handle)

    assert result["result"] == "fail"
    assert result["evaluated_value"]["internal_decoded_samples"] == 80
    assert result["evaluated_value"]["internal_negotiation_accepted_samples"] == 0


def test_boundary_assertion_fails_on_under_sample_or_lost_progress(tmp_path):
    _boundary_result(tmp_path, malformed_samples=4, progressed=False)
    handle = type("Handle", (), {"run_dir": tmp_path})()

    result = AmaruMeasurementBoundaryProven(
        params={
            "expected_mode": "patched",
            "min_attempts_per_case": 40,
            "min_internal_samples_per_outcome": 30,
        }
    ).evaluate(handle)

    assert result["result"] == "fail"
    assert "honest_chain_progressed" in result["evaluated_value"]["failed_checks"]
    assert result["evaluated_value"]["internal_malformed_samples"] == 4


def test_calibration_primitive_runs_retained_leg_with_exact_attempt_count(
    tmp_path, monkeypatch
):
    calls = []

    class Handle:
        run_dir = tmp_path

        def __init__(self):
            self.events = []

        def log(self, **event):
            self.events.append(event)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output_dir = Path(command[command.index("--output-dir") + 1])
        (output_dir / "result.json").write_text(
            json.dumps({"attempts": {"total": 40}}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    handle = Handle()
    primitive = RuntimeAmaruMeasurementCalibration(
        params={"runtime_root": "/runtime", "attempts": 40}
    )

    primitive.run(handle, None)

    command, kwargs = calls[0]
    assert command[2:4] == ["leg", "--runtime-root"]
    assert command[command.index("--attempts") + 1] == "40"
    assert kwargs["cwd"] == ROOT / "dwarf"
    completed = next(
        event for event in handle.events if event["event"] == "completed"
    )
    assert completed["payload"]["outcome"] == "ok"
    assert completed["payload"]["report"]["attempts"]["total"] == 40


def test_calibration_primitive_emits_outcome_independent_workload_accounting(
    tmp_path, monkeypatch
):
    class Handle:
        run_dir = tmp_path

        def __init__(self):
            self.events = []

        def log(self, **event):
            self.events.append(event)

    attempts = [
        {
            "attempt_id": "a",
            "outcome": "rejected",
            "elapsed_micros": 12,
            "request_length": 8,
        },
        {
            "attempt_id": "b",
            "outcome": "timeout",
            "elapsed_micros": 34,
            "request_length": 8,
        },
    ]

    def run(command, **kwargs):
        output_dir = Path(command[command.index("--output-dir") + 1])
        (output_dir / "attempts.ndjson").write_text(
            "".join(json.dumps(row) + "\n" for row in attempts),
            encoding="utf-8",
        )
        (output_dir / "result.json").write_text(
            json.dumps({
                "attempts": {"total": 2, "outcomes": {"rejected": 1, "timeout": 1}},
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    handle = Handle()
    primitive = RuntimeAmaruMeasurementCalibration(
        params={"runtime_root": "/runtime", "attempts": 40}
    )

    primitive.run(handle, None)

    accounting = next(
        event for event in handle.events if event["event"] == "workload_accounting"
    )
    assert accounting["payload"] == {
        "attempted": 2,
        "successful": 0,
        "rejected": 1,
        "bytes": 16,
        "batches": 2,
        "backlog": None,
        "attempts": [
            {"input_id": "a", "outcome": "rejected", "elapsed_micros": 12},
            {"input_id": "b", "outcome": "timeout", "elapsed_micros": 34},
        ],
    }
