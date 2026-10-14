import json

import pytest

from scripts.runtime_amaru_measurement_calibration import (
    ACCEPTANCE_CASE_SET,
    build_attempt_record,
    build_case_plan,
    build_handshake_frame,
    build_result_context,
    build_workload_identity,
    classify_case_response,
    classify_unsupported_handshake_response,
    summarize_attempts,
)
from scripts import runtime_amaru_measurement_calibration as calibration


def _mux_response(payload: bytes) -> bytes:
    return b"\x00\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload


def _docker_inspect_result(networks):
    return type(
        "Result",
        (),
        {
            "returncode": 0,
            "stdout": json.dumps([{"NetworkSettings": {"Networks": networks}}]),
            "stderr": "",
        },
    )()


def test_calibration_selects_target_ip_on_verified_attached_run_network(monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_RUN_NETWORK", "topology-default")
    monkeypatch.setattr(
        calibration,
        "_run",
        lambda *_args, **_kwargs: _docker_inspect_result(
            {
                "topology-amaru-consumer-net": {"IPAddress": "10.192.48.3"},
                "topology-default": {"IPAddress": "10.192.47.11"},
            }
        ),
    )

    assert calibration._container_ip("topology-amaru-relay-1-1") == "10.192.47.11"


def test_stock_target_identity_keeps_manifest_digest_distinct_from_config_id():
    manifest_digest = "sha256:" + "1" * 64
    config_id = "sha256:" + "2" * 64
    image = f"ghcr.io/pragma-org/amaru:v10.11.20260918@{manifest_digest}"
    runtime = {
        "versions": {
            "catalog_snapshot": {
                "selected_releases": [
                    {
                        "implementation": "amaru",
                        "version": "10.11.20260918",
                        "source_revision": "a" * 40,
                        "artifacts": [
                            {
                                "kind": "oci",
                                "availability": "available",
                                "digest": manifest_digest,
                            }
                        ],
                    }
                ]
            }
        },
        "images": {"amaru": image},
        "identity": {
            "services": {
                "amaru-relay-1": {
                    "matched": True,
                    "expected_image": image,
                    "expected_image_id": config_id,
                    "artifact_image_id": config_id,
                }
            }
        },
    }

    identity = calibration._target_identity(runtime)

    assert identity["image_digest"] == manifest_digest


def _local_calibration_runtime():
    config_id = "sha256:" + "2" * 64
    registration = {
        "registration_id": "registration-local",
        "implementation": "amaru",
        "version": "local-amaru",
        "config_image_id": config_id,
        "executable_digest": "sha256:" + "3" * 64,
        "platform": "linux/amd64",
        "controller_installation_id": "install-test",
        "provenance": {"source_revision": None, "qualification": "unverified"},
    }
    return {
        "profile_id": "profile-local",
        "versions": {
            "catalog_revision": "a" * 64,
            "catalog_snapshot": {
                "operator_registration_id": "registration-local",
                "operator_overlay_revision": "sha256:" + "4" * 64,
                "selected_releases": [{
                    "implementation": "amaru",
                    "version": "local-amaru",
                    "source_revision": None,
                    "operator_registration": registration,
                    "artifacts": [{"kind": "oci", "availability": "available", "digest": None}],
                }],
            },
        },
        "images": {"amaru": config_id},
        "identity": {
            "matched": True,
            "services": {"amaru-relay-1": {
                "matched": True,
                "expected_image": config_id,
                "expected_image_id": config_id,
                "artifact_image_id": config_id,
            }},
        },
    }


def test_local_calibration_preserves_proven_config_and_executable_without_manifest():
    identity = calibration._target_identity(_local_calibration_runtime())
    assert identity["identity_kind"] == "operator-local-config"
    assert identity["image_digest"] is None
    assert identity["image_config_id"] == "sha256:" + "2" * 64
    assert identity["executable_digest"] == "sha256:" + "3" * 64
    assert identity["source_revision"] is None
    assert identity["mode"] == "stock"


@pytest.mark.parametrize("mutation", ["image", "unmatched", "executable", "platform"])
def test_local_calibration_rejects_unproven_identity(mutation):
    runtime = _local_calibration_runtime()
    service = runtime["identity"]["services"]["amaru-relay-1"]
    registration = runtime["versions"]["catalog_snapshot"]["selected_releases"][0]["operator_registration"]
    if mutation == "image":
        service["artifact_image_id"] = "sha256:" + "9" * 64
    elif mutation == "unmatched":
        service["matched"] = False
    elif mutation == "executable":
        registration["executable_digest"] = None
    else:
        registration["platform"] = "windows/amd64"
    with pytest.raises(RuntimeError):
        calibration._target_identity(runtime)


def test_calibration_target_ip_fails_closed_without_exact_multi_network_binding(
    monkeypatch,
):
    monkeypatch.setattr(
        calibration,
        "_run",
        lambda *_args, **_kwargs: _docker_inspect_result(
            {
                "topology-amaru-consumer-net": {"IPAddress": "10.192.48.3"},
                "topology-default": {"IPAddress": "10.192.47.11"},
            }
        ),
    )

    with pytest.raises(RuntimeError, match="network identity is ambiguous"):
        calibration._container_ip("topology-amaru-relay-1-1")
    monkeypatch.setenv("ADA2_DWARF_RUN_NETWORK", "foreign-network")
    with pytest.raises(RuntimeError, match="attached run network is absent"):
        calibration._container_ip("topology-amaru-relay-1-1")


def test_attempt_record_retains_bounded_wire_transcript_and_every_elapsed_time():
    request = bytes.fromhex("0000000100000000000000028200")
    response = bytes.fromhex("8202a100")

    record = build_attempt_record(
        attempt_id="handshake-0000",
        outcome="rejected",
        detail="refuse-response",
        elapsed_micros=123,
        request=request,
        response=response,
    )

    assert record["elapsed_micros"] == 123
    assert record["request_hex"] == request.hex()
    assert record["request_length"] == len(request)
    assert record["response_hex"] == response.hex()
    assert record["response_length"] == len(response)
    assert record["request_sha256"].startswith("sha256:")
    assert record["response_sha256"].startswith("sha256:")


def test_result_context_records_timing_policy_run_identity_and_hardware(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "scripts.runtime_amaru_measurement_calibration.platform.uname",
        lambda: type("Uname", (), {"system": "Linux", "release": "test", "machine": "x86_64"})(),
    )
    monkeypatch.setattr(
        "scripts.runtime_amaru_measurement_calibration.os.cpu_count", lambda: 8
    )
    monkeypatch.setattr(
        "scripts.runtime_amaru_measurement_calibration._physical_memory_bytes",
        lambda: 16_000,
    )

    result = build_result_context(output_dir=tmp_path / "run-123")

    assert result["run_id"] == "run-123"
    assert result["timing_policy"] == {
        "clock": "monotonic-perf-counter-ns",
        "start": "before-tcp-connect",
        "stop": "terminal-response-eof-timeout-or-disconnect",
        "warmup": "none",
        "outcome_inclusion": "all",
    }
    assert result["hardware"] == {
        "system": "Linux",
        "kernel_release": "test",
        "architecture": "x86_64",
        "logical_cpu_count": 8,
        "physical_memory_bytes": 16_000,
    }
    assert result["runner"]["script_sha256"].startswith("sha256:")


def test_calibration_attempt_summary_keeps_every_terminal_outcome_and_elapsed_time():
    attempts = [
        {"attempt_id": "a0", "outcome": "rejected", "elapsed_micros": 10},
        {"attempt_id": "a1", "outcome": "timeout", "elapsed_micros": 30},
        {"attempt_id": "a2", "outcome": "disconnected", "elapsed_micros": 20},
        {"attempt_id": "a3", "outcome": "unclassified", "elapsed_micros": 40},
    ]

    result = summarize_attempts(attempts)

    assert result["combined"]["sample_count"] == 4
    assert result["combined"]["mean"] == 25.0
    assert set(result["by_outcome"]) == {
        "rejected",
        "timeout",
        "disconnected",
        "unclassified",
    }
    assert all(row["sample_count"] == 1 for row in result["by_outcome"].values())


def test_calibration_workload_identity_is_mode_independent_and_exact():
    left = build_workload_identity(attempt_count=40, seed="0xA11CE501")
    right = build_workload_identity(attempt_count=40, seed="0xA11CE501")

    assert left == right
    assert left["attempt_count"] == 40
    assert left["transport"] == "tcp"
    assert left["target_port"] == 3000
    assert left["mux_direction_bit"] == 0
    assert left["response_cap_bytes"] == 256
    assert left["workload_digest"].startswith("sha256:")


def test_seed_changes_only_workload_identity_not_fixed_case_payloads():
    left = build_workload_identity(
        attempt_count=120, seed="1", case_set=ACCEPTANCE_CASE_SET
    )
    right = build_workload_identity(
        attempt_count=120, seed="2", case_set=ACCEPTANCE_CASE_SET
    )

    assert left["cases"] == right["cases"]
    assert left["workload_digest"] != right["workload_digest"]


def test_run_leg_persists_the_explicit_canonical_seed(monkeypatch, tmp_path):
    manifest = "sha256:" + "1" * 64
    config_id = "sha256:" + "2" * 64
    image = f"ghcr.io/pragma-org/amaru:test@{manifest}"
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "runtime.json").write_text(
        json.dumps(
            {
                "compose_project": "seed-test",
                "network_magic": 42,
                "actual_topology": {"amaru_services": ["amaru-relay-1"]},
                "images": {"amaru": image},
                "identity": {
                    "services": {
                        "amaru-relay-1": {
                            "matched": True,
                            "expected_image": image,
                            "expected_image_id": config_id,
                            "artifact_image_id": config_id,
                        }
                    }
                },
                "versions": {
                    "catalog_snapshot": {
                        "selected_releases": [
                            {
                                "implementation": "amaru",
                                "version": "10.11.test",
                                "source_revision": "a" * 40,
                                "artifacts": [
                                    {
                                        "kind": "oci",
                                        "availability": "available",
                                        "digest": manifest,
                                    }
                                ],
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    healthy = {
        "running": True,
        "status": "running",
        "exit_code": 0,
        "oom_killed": False,
        "restart_count": 0,
    }
    monkeypatch.setattr(calibration, "_compose_container", lambda *_args: "container")
    monkeypatch.setattr(calibration, "_container_ip", lambda *_args: "127.0.0.1")
    monkeypatch.setattr(calibration, "_container_state", lambda *_args: dict(healthy))
    monkeypatch.setattr(
        calibration, "_latest_tip", lambda *_args: {"block_height": 1, "hash": "a"}
    )
    monkeypatch.setattr(
        calibration,
        "_wait_for_progress",
        lambda *_args, **_kwargs: {"block_height": 2, "hash": "b"},
    )
    monkeypatch.setattr(
        calibration,
        "run_attempts",
        lambda **_kwargs: [
            {
                "attempt_id": "accepted",
                "case": "supported-version-acceptance",
                "outcome": "accepted",
                "expected_external_outcome": "accepted",
                "elapsed_micros": 1,
            },
            {
                "attempt_id": "unsupported",
                "case": "unsupported-version-refusal",
                "outcome": "rejected",
                "expected_external_outcome": "rejected",
                "elapsed_micros": 2,
            },
            {
                "attempt_id": "malformed",
                "case": "malformed-cbor-rejection",
                "outcome": "rejected",
                "expected_external_outcome": "rejected",
                "elapsed_micros": 3,
            },
        ],
    )
    monkeypatch.setattr(
        calibration,
        "_capture_logs",
        lambda _container, *, since, destination: (
            destination.parent.mkdir(parents=True, exist_ok=True),
            destination.write_text("", encoding="utf-8"),
        ),
    )
    monkeypatch.setattr(calibration, "classify_log_signals", lambda _text: {"fatal": []})

    result = calibration.run_leg(
        runtime_root=runtime_root,
        output_dir=tmp_path / "output",
        attempt_count=3,
        timeout_seconds=0.1,
        case_set=ACCEPTANCE_CASE_SET,
        seed="2703029506",
    )

    assert result["workload_identity"]["seed"] == "2703029506"
    persisted = json.loads((tmp_path / "output" / "result.json").read_text())
    assert persisted["workload_identity"]["seed"] == "2703029506"


@pytest.mark.parametrize("seed", ["-1", "1.5", "0x", str(1 << 64)])
def test_helper_rejects_invalid_explicit_seed_before_run_leg(monkeypatch, seed):
    monkeypatch.setattr(
        calibration,
        "run_leg",
        lambda **_kwargs: pytest.fail("runtime leg must not start"),
    )

    with pytest.raises(SystemExit):
        calibration.main(
            [
                "leg",
                "--runtime-root",
                "/runtime",
                "--output-dir",
                "/output",
                "--seed",
                seed,
            ]
        )


def test_calibration_connects_to_the_inbound_responder_mux_direction():
    frame = build_handshake_frame()
    header_word = int.from_bytes(frame[4:8], "big")

    assert header_word >> 31 == 0
    assert header_word & 0x7FFF0000 == 0


def test_acceptance_case_plan_is_balanced_and_reaches_all_decode_outcomes():
    plan = build_case_plan(attempt_count=120, case_set=ACCEPTANCE_CASE_SET)

    assert len(plan) == 120
    counts = {
        case: sum(1 for row in plan if row["name"] == case)
        for case in {row["name"] for row in plan}
    }
    assert counts == {
        "supported-version-acceptance": 40,
        "unsupported-version-refusal": 40,
        "malformed-cbor-rejection": 40,
    }
    assert {row["expected_external_outcome"] for row in plan} == {
        "accepted",
        "rejected",
    }
    assert {row["expected_decode_outcome"] for row in plan} == {
        "decoded",
        "malformed",
    }


def test_supported_case_uses_source_exact_v15_four_field_offer():
    plan = build_case_plan(attempt_count=3, case_set=ACCEPTANCE_CASE_SET)
    supported = next(
        row for row in plan if row["name"] == "supported-version-acceptance"
    )

    assert supported["payload_hex"] == "8200a10f84182af400f4"
    assert build_handshake_frame(supported["payload_hex"])[8:] == bytes.fromhex(
        "8200a10f84182af400f4"
    )


def test_unsupported_case_uses_source_exact_v15_four_field_offer():
    plan = build_case_plan(attempt_count=3, case_set=ACCEPTANCE_CASE_SET)
    unsupported = next(
        row for row in plan if row["name"] == "unsupported-version-refusal"
    )

    assert unsupported["payload_hex"] == "8200a11903e784182af400f4"
    assert build_handshake_frame(unsupported["payload_hex"])[8:] == bytes.fromhex(
        "8200a11903e784182af400f4"
    )


def test_acceptance_workload_identity_pins_every_case_and_wire_payload():
    identity = build_workload_identity(
        attempt_count=120,
        seed="0xA11CE502",
        case_set=ACCEPTANCE_CASE_SET,
    )

    assert identity["case_set"] == ACCEPTANCE_CASE_SET
    assert identity["attempt_count"] == 120
    assert [row["attempt_count"] for row in identity["cases"]] == [40, 40, 40]
    assert all(row["payload_hex"] for row in identity["cases"])
    assert identity["workload_digest"].startswith("sha256:")


def test_unsupported_handshake_eof_and_response_are_both_retained_as_rejections():
    assert classify_unsupported_handshake_response(b"") == ("rejected", "eof")
    assert classify_unsupported_handshake_response(b"refuse") == (
        "rejected",
        "refuse-response",
    )


def test_supported_case_requires_an_actual_handshake_accept_message():
    accepted = _mux_response(bytes.fromhex("83010a182a"))
    refused = _mux_response(bytes.fromhex("820280"))

    assert classify_case_response("supported-version-acceptance", accepted) == (
        "accepted",
        "accept-response",
    )
    assert classify_case_response("supported-version-acceptance", refused) == (
        "rejected",
        "refuse-response",
    )
    assert classify_case_response("supported-version-acceptance", b"not-a-mux-frame") == (
        "unclassified",
        "invalid-handshake-response",
    )
    assert classify_case_response("unsupported-version-refusal", accepted) == (
        "accepted",
        "accept-response",
    )


def test_mux_response_reader_retains_a_fragmented_complete_frame():
    frame = _mux_response(bytes.fromhex("83010a182a"))

    class FragmentedSocket:
        def __init__(self):
            self.fragments = [frame[:3], frame[3:8], frame[8:10], frame[10:]]

        def recv(self, size):
            fragment = self.fragments.pop(0) if self.fragments else b""
            assert len(fragment) <= size
            return fragment

    assert calibration._recv_mux_frame(FragmentedSocket()) == frame
