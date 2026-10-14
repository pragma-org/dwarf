import json
from pathlib import Path

import pytest

from profile_manager.measurement_execution import (
    MeasurementExecutionError,
    prepare_scenario_measurements,
)
from profile_manager.measurement_resolution import MeasurementResolutionError, resolve_measurements
from profile_manager.scenario import load_scenario
from profile_manager.scenario import scenario_from_body
from profile_manager import scenario as scenario_module
from profile_manager.launch_store import create_launch
from profile_manager.run_plan import RunPlanRequest, resolve_run_plan


AMARU_VERSION = "10.11.20260912"
AMARU_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
AMARU_DIGEST = "sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e"
AMARU_CONFIG_ID = "sha256:c536d7c3cde68fb5728994ebdea91cf63f4e1d9281d0d0983c42bbfc582f8139"
AMARU_PATCHED_DIGEST = "sha256:dacb2351e69ab1d0bbfbc569b222d1bde40d9a158e79c556b30df554375addcc"
AMARU_PATCHED_CONFIG_ID = "sha256:8f5fdbdfc5b7dfa03b13ad99fd7f985012315767e25cfdb9c76c681e3f6ea857"
AMARU_PATCHED_EXECUTABLE = "sha256:b0cebe917e092c3d4e47ac4841de7a49338dca1e586616803fe17e92d0d93425"
AMARU_PATCH_SET = "f0e1aebca9adf2713d4d9f6f8ba33f20b0d04c3b35de6127d4a1e027a68b50af"
AMARU_FIXED_VERSION = "v10.11.20260912-30-gd3a6dafc"
AMARU_FIXED_REVISION = "d3a6dafcced78f5809a96619e883cf04911d2bdc"
AMARU_NANOSECOND_PATCH_SET = "4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0"
LOCAL_CONFIG_ID = "sha256:" + "2" * 64
LOCAL_EXECUTABLE = "sha256:" + "3" * 64
LOCAL_OVERLAY = "sha256:" + "4" * 64
LOCAL_REGISTRATION = "registration-97900317c3844f0882e515b1"


def _runtime(
    path: Path,
    *,
    matched=True,
    digest=AMARU_CONFIG_ID,
    amaru_json_traces=True,
):
    body = {
        "schema_version": 1,
        "profile_id": "profile-r-amaru-measurement-stock-control",
        "compose_project": "dwarf-profile-r-amaru-measurement-stock-control",
        "versions": {
            "amaru": AMARU_VERSION,
            "catalog_revision": "a" * 64,
            "catalog_snapshot": {
                "catalog_revision": "a" * 64,
                "selected_releases": [
                    {
                        "implementation": "amaru",
                        "version": AMARU_VERSION,
                        "source_revision": AMARU_REVISION,
                        "artifacts": [
                            {
                                "kind": "oci",
                                "availability": "available",
                                "reference": "ghcr.io/pragma-org/amaru:v10.11.20260912",
                                "digest": AMARU_DIGEST,
                            }
                        ],
                    }
                ],
            },
        },
        "images": {
            "amaru": f"ghcr.io/pragma-org/amaru:v10.11.20260912@{AMARU_DIGEST}"
        },
        "identity": {
            "matched": matched,
            "services": {
                "amaru-relay-1": {
                    "container": "dwarf-profile-r-amaru-relay-1",
                    "expected_version": AMARU_VERSION,
                    "expected_image": f"ghcr.io/pragma-org/amaru:v10.11.20260912@{AMARU_DIGEST}",
                    "expected_image_id": AMARU_CONFIG_ID,
                    "artifact_image_id": digest,
                    "matched": matched,
                }
            },
        },
        "measurement_target": None,
        "amaru_json_traces": amaru_json_traces,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _local_scenario():
    body = {
        "spec_version": "v1",
        "id": "amaru-local-experimental-measurement",
        "title": "Amaru local experimental measurement",
        "target": {"implementation": "amaru", "version": "local-amaru"},
        "runtime": "devnet",
        "profile": "profile-r-amaru-measurement-stock-control",
        "measurement_profile": "none",
        "measurements": [
            {"id": "amaru-external-workload-accounting", "enabled": True}
        ],
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    return scenario_from_body((json.dumps(body) + "\n").encode())


def _local_identity():
    return {
        "implementation": "amaru",
        "version": "local-amaru",
        "source_revision": None,
        "mode": "stock",
        "identity_kind": "operator-local-config",
        "image_reference": LOCAL_CONFIG_ID,
        "image_digest": None,
        "image_config_id": LOCAL_CONFIG_ID,
        "executable_digest": LOCAL_EXECUTABLE,
        "platform": "linux/amd64",
        "operator_registration_id": LOCAL_REGISTRATION,
        "operator_overlay_revision": LOCAL_OVERLAY,
        "controller_installation_id": "install-test",
        "version_catalog_revision": "a" * 64,
    }


def _local_runtime(path: Path, *, config_id=LOCAL_CONFIG_ID):
    registration = {
        "registration_id": LOCAL_REGISTRATION,
        "implementation": "amaru",
        "version": "local-amaru",
        "config_image_id": LOCAL_CONFIG_ID,
        "platform": "linux/amd64",
        "controller_installation_id": "install-test",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    body = {
        "schema_version": 1,
        "profile_id": "profile-r-amaru-measurement-stock-control",
        "versions": {
            "catalog_revision": "a" * 64,
            "catalog_snapshot": {
                "catalog_revision": "a" * 64,
                "operator_registration_id": LOCAL_REGISTRATION,
                "operator_overlay_revision": LOCAL_OVERLAY,
                "selected_releases": [
                    {
                        "implementation": "amaru",
                        "version": "local-amaru",
                        "source_revision": None,
                        "operator_registration": registration,
                        "artifacts": [
                            {
                                "kind": "oci",
                                "availability": "available",
                                "reference": LOCAL_CONFIG_ID,
                                "digest": None,
                                "config_id": LOCAL_CONFIG_ID,
                            }
                        ],
                    }
                ],
            },
        },
        "images": {"amaru": LOCAL_CONFIG_ID},
        "identity": {
            "matched": True,
            "services": {
                "amaru-relay-1": {
                    "container": "dwarf-local-amaru-relay-1",
                    "expected_image": LOCAL_CONFIG_ID,
                    "expected_image_id": LOCAL_CONFIG_ID,
                    "artifact_image_id": config_id,
                    "matched": True,
                }
            },
        },
        "measurement_target": None,
        "amaru_json_traces": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_prepare_local_measurements_revalidates_frozen_consent_against_live_config(
    tmp_path,
):
    scenario = _local_scenario()
    identity = _local_identity()
    with pytest.raises(MeasurementResolutionError) as raised:
        resolve_measurements(
            scenario,
            target_identity=identity,
            capabilities={"dwarf-workload-events"},
        )
    consent = raised.value.resolution["experimental_consent_required"]
    frozen = resolve_measurements(
        scenario,
        target_identity=identity,
        capabilities={"dwarf-workload-events"},
        experimental_consent=consent,
    )
    runtime_path = _local_runtime(tmp_path / "runtime.json")

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
        expected_resolution=frozen,
    )

    assert prepared.resolution["target_identity"] == identity
    assert prepared.resolution["experimental_consent"]["context_digest"] == consent[
        "context_digest"
    ]

    _local_runtime(runtime_path, config_id="sha256:" + "9" * 64)
    with pytest.raises(MeasurementExecutionError, match="config identity"):
        prepare_scenario_measurements(
            scenario,
            runtime_metadata_path=runtime_path,
            tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
            expected_resolution=frozen,
        )


def test_local_measurements_allow_unrelated_overlay_advance_with_exact_identity(tmp_path):
    scenario = _local_scenario()
    identity = _local_identity()
    identity["operator_overlay_revision"] = "sha256:" + "8" * 64
    with pytest.raises(MeasurementResolutionError) as raised:
        resolve_measurements(
            scenario, target_identity=identity, capabilities={"dwarf-workload-events"}
        )
    consent = raised.value.resolution["experimental_consent_required"]
    frozen = resolve_measurements(
        scenario,
        target_identity=identity,
        capabilities={"dwarf-workload-events"},
        experimental_consent=consent,
    )
    runtime_path = _local_runtime(tmp_path / "runtime.json")
    runtime = json.loads(runtime_path.read_text())
    runtime["versions"]["catalog_snapshot"]["selected_releases"][0][
        "operator_registration"
    ]["executable_digest"] = LOCAL_EXECUTABLE
    runtime_path.write_text(json.dumps(runtime))

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
        expected_resolution=frozen,
    )

    assert prepared.resolution["target_identity"] == identity
    assert prepared.resolution["experimental_consent"]["context_digest"] == consent[
        "context_digest"
    ]
    assert json.loads(runtime_path.read_text())["versions"]["catalog_snapshot"][
        "operator_overlay_revision"
    ] == LOCAL_OVERLAY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable_digest", "sha256:" + "9" * 64),
        ("controller_installation_id", "install-other"),
        ("platform", "linux/arm64"),
        ("source_revision", "9" * 40),
        ("operator_registration_id", "registration-other"),
        ("version_catalog_revision", "9" * 64),
        ("operator_overlay_revision", "invalid-revision"),
    ],
)
def test_overlay_advance_does_not_allow_changed_local_identity(tmp_path, field, value):
    scenario = _local_scenario()
    identity = _local_identity()
    identity["operator_overlay_revision"] = "sha256:" + "8" * 64
    with pytest.raises(MeasurementResolutionError) as raised:
        resolve_measurements(
            scenario, target_identity=identity, capabilities={"dwarf-workload-events"}
        )
    frozen = resolve_measurements(
        scenario,
        target_identity=identity,
        capabilities={"dwarf-workload-events"},
        experimental_consent=raised.value.resolution["experimental_consent_required"],
    )
    runtime_path = _local_runtime(tmp_path / "runtime.json")
    runtime = json.loads(runtime_path.read_text())
    runtime["versions"]["catalog_snapshot"]["selected_releases"][0][
        "operator_registration"
    ]["executable_digest"] = LOCAL_EXECUTABLE
    runtime_path.write_text(json.dumps(runtime))
    frozen["target_identity"][field] = value

    with pytest.raises(MeasurementExecutionError, match="does not match frozen launch"):
        prepare_scenario_measurements(
            scenario,
            runtime_metadata_path=runtime_path,
            tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
            expected_resolution=frozen,
        )


def test_overlay_advance_still_requires_consent_for_frozen_revision(tmp_path):
    scenario = _local_scenario()
    identity = _local_identity()
    with pytest.raises(MeasurementResolutionError) as raised:
        resolve_measurements(
            scenario, target_identity=identity, capabilities={"dwarf-workload-events"}
        )
    frozen = resolve_measurements(
        scenario,
        target_identity=identity,
        capabilities={"dwarf-workload-events"},
        experimental_consent=raised.value.resolution["experimental_consent_required"],
    )
    frozen["target_identity"]["operator_overlay_revision"] = "sha256:" + "8" * 64
    runtime_path = _local_runtime(tmp_path / "runtime.json")
    runtime = json.loads(runtime_path.read_text())
    runtime["versions"]["catalog_snapshot"]["selected_releases"][0][
        "operator_registration"
    ]["executable_digest"] = LOCAL_EXECUTABLE
    runtime_path.write_text(json.dumps(runtime))

    with pytest.raises(MeasurementExecutionError):
        prepare_scenario_measurements(
            scenario,
            runtime_metadata_path=runtime_path,
            tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
            expected_resolution=frozen,
        )


def _patched_runtime(path: Path, *, amaru_json_traces=True, **overrides):
    measurement_target = {
        "implementation": "amaru",
        "target_mode": "patched",
        "version": AMARU_VERSION,
        "source_revision": AMARU_REVISION,
        "patch_set_sha256": AMARU_PATCH_SET,
        "image": f"dwarf/amaru-measurement@{AMARU_PATCHED_DIGEST}",
        "image_digest": AMARU_PATCHED_CONFIG_ID,
        "executable_digest": AMARU_PATCHED_EXECUTABLE,
        "build_result_sha256": "sha256:" + "b" * 64,
        "runtime_probe_log_sha256": "sha256:" + "c" * 64,
    }
    measurement_target.update(overrides)
    body = {
        "schema_version": 1,
        "profile_id": "profile-q-amaru-measurement-patched",
        "compose_project": "dwarf-profile-q-amaru-measurement-patched",
        "runtime_root": str(path.parent),
        "versions": {
            "amaru": AMARU_VERSION,
            "catalog_revision": "a" * 64,
            "catalog_snapshot": {
                "catalog_revision": "a" * 64,
                "selected_releases": [
                    {
                        "implementation": "amaru",
                        "version": AMARU_VERSION,
                        "source_revision": AMARU_REVISION,
                        "artifacts": [
                            {
                                "kind": "oci",
                                "availability": "available",
                                "reference": "ghcr.io/pragma-org/amaru:v10.11.20260912",
                                "digest": AMARU_DIGEST,
                            }
                        ],
                    }
                ],
            },
        },
        "identity": {
            "matched": True,
            "services": {
                "amaru-relay-1": {
                    "container": "dwarf-profile-q-amaru-measurement-patched-amaru-relay-1-1",
                    "matched": True,
                    "expected_image": f"dwarf/amaru-measurement@{AMARU_PATCHED_DIGEST}",
                    "expected_image_id": AMARU_PATCHED_CONFIG_ID,
                    "artifact_image_id": AMARU_PATCHED_CONFIG_ID,
                    "executable_digest_reported": AMARU_PATCHED_EXECUTABLE,
                    "executable_digest_matched": True,
                }
            },
        },
        "measurement_target": measurement_target,
        "amaru_json_traces": amaru_json_traces,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_prepare_scenario_measurements_resolves_exact_live_stock_runtime(tmp_path):
    scenario = load_scenario("dwarf/scenarios/amaru-measurement-e2e-stock.yaml")
    runtime_path = _runtime(tmp_path / "runtime.json")

    prepared = prepare_scenario_measurements(
        scenario, runtime_metadata_path=runtime_path
    )

    identity = prepared.resolution["target_identity"]
    assert identity == {
        "implementation": "amaru",
        "version": AMARU_VERSION,
        "source_revision": AMARU_REVISION,
        "mode": "stock",
        "image_reference": f"ghcr.io/pragma-org/amaru:v10.11.20260912@{AMARU_DIGEST}",
        "image_digest": AMARU_DIGEST,
        "executable_digest": None,
        "version_catalog_revision": "a" * 64,
    }
    assert prepared.resolution["incompatible"] == []
    assert prepared.resolution["skipped"] == []
    assert len(prepared.resolution["resolved"]) == 11


def test_prepare_scenario_measurements_fails_closed_on_unproven_runtime(tmp_path):
    scenario = load_scenario("dwarf/scenarios/amaru-measurement-e2e-stock.yaml")
    runtime_path = _runtime(tmp_path / "runtime.json", matched=False)

    with pytest.raises(MeasurementExecutionError, match="identity is not proven"):
        prepare_scenario_measurements(scenario, runtime_metadata_path=runtime_path)

    runtime_path = _runtime(tmp_path / "runtime.json", digest="sha256:" + "9" * 64)
    with pytest.raises(MeasurementExecutionError, match="image config identity"):
        prepare_scenario_measurements(scenario, runtime_metadata_path=runtime_path)


def test_prepare_scenario_measurements_rejects_stock_traces_when_runtime_did_not_enable_json(
    tmp_path,
):
    scenario = load_scenario("dwarf/scenarios/amaru-measurement-e2e-stock.yaml")
    runtime_path = _runtime(
        tmp_path / "runtime.json",
        amaru_json_traces=False,
    )

    with pytest.raises(
        MeasurementExecutionError,
        match="missing capabilities: .*amaru-json-traces",
    ):
        prepare_scenario_measurements(scenario, runtime_metadata_path=runtime_path)


def test_prepare_scenario_measurements_resolves_exact_live_patched_runtime(tmp_path):
    scenario = load_scenario(
        "dwarf/scenarios/amaru-measurement-overhead-calibration-patched.yaml"
    )
    runtime_path = _patched_runtime(tmp_path / "runtime.json")

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )

    identity = prepared.resolution["target_identity"]
    assert identity == {
        "implementation": "amaru",
        "version": AMARU_VERSION,
        "source_revision": AMARU_REVISION,
        "mode": "patched",
        "patch_set_sha256": AMARU_PATCH_SET,
        "measurement_revision": "whole-microseconds-v1",
        "image_reference": f"dwarf/amaru-measurement@{AMARU_PATCHED_DIGEST}",
        "image_digest": AMARU_PATCHED_CONFIG_ID,
        "executable_digest": AMARU_PATCHED_EXECUTABLE,
        "build_result_sha256": "sha256:" + "b" * 64,
        "runtime_probe_log_sha256": "sha256:" + "c" * 64,
        "version_catalog_revision": "a" * 64,
    }
    assert prepared.resolution["incompatible"] == []
    assert prepared.resolution["skipped"] == []
    assert len(prepared.resolution["resolved"]) == 14

    factories = prepared.build_factories(tmp_path / "run")
    collector = factories["amaru-patched-protocol-decode"](
        next(
            entry
            for entry in prepared.resolution["resolved"]
            if entry["id"] == "amaru-patched-protocol-decode"
        )
    )
    assert collector.target_identity == identity


def test_prepare_measurements_accepts_verified_imported_config_content_id(tmp_path):
    scenario = load_scenario(
        "dwarf/scenarios/amaru-measurement-overhead-calibration-patched.yaml"
    )
    runtime_path = _patched_runtime(tmp_path / "runtime.json")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    target = runtime["measurement_target"]
    target["source_image_reference"] = target["image"]
    target["image_manifest_digest"] = AMARU_PATCHED_DIGEST
    target["image"] = AMARU_PATCHED_CONFIG_ID
    target["image_config_digest"] = AMARU_PATCHED_CONFIG_ID
    target["qualification_image_id"] = AMARU_PATCHED_DIGEST
    target["transfer_source_installation_id"] = "install-source"
    target["transfer_manifest_sha256"] = "sha256:" + "d" * 64
    service = runtime["identity"]["services"]["amaru-relay-1"]
    service["expected_image"] = AMARU_PATCHED_CONFIG_ID
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )

    identity = prepared.resolution["target_identity"]
    assert identity["image_reference"] == AMARU_PATCHED_CONFIG_ID
    assert identity["image_manifest_digest"] == AMARU_PATCHED_DIGEST
    assert identity["image_config_digest"] == AMARU_PATCHED_CONFIG_ID
    assert identity["qualification_image_id"] == AMARU_PATCHED_DIGEST
    assert identity["transfer_source_installation_id"] == "install-source"
    assert identity["image_digest"] == AMARU_PATCHED_CONFIG_ID


def test_no_trace_patched_runtime_does_not_poison_later_traced_capabilities(tmp_path):
    scenario = load_scenario(
        "dwarf/scenarios/amaru-measurement-overhead-calibration-patched.yaml"
    )
    without_traces = _patched_runtime(
        tmp_path / "without-traces.json",
        amaru_json_traces=False,
    )
    with pytest.raises(MeasurementExecutionError, match="missing capabilities"):
        prepare_scenario_measurements(
            scenario,
            runtime_metadata_path=without_traces,
            tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
        )

    with_traces = _patched_runtime(tmp_path / "with-traces.json")
    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=with_traces,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )

    assert len(prepared.resolution["resolved"]) == 14


def test_prepare_scenario_measurements_accepts_fixed_revision_nanosecond_target(tmp_path):
    scenario = load_scenario(
        "dwarf/scenarios/client-example-cbor-decoding-amaru-d3a6dafc-regression.yaml"
    )
    runtime_path = _patched_runtime(tmp_path / "runtime.json")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["profile_id"] = scenario.profile
    runtime["measurement_target"].update(
        {
            "version": AMARU_FIXED_VERSION,
            "source_revision": AMARU_FIXED_REVISION,
            "patch_set_sha256": AMARU_NANOSECOND_PATCH_SET,
        }
    )
    release = runtime["versions"]["catalog_snapshot"]["selected_releases"][0]
    release["version"] = AMARU_FIXED_VERSION
    release["source_revision"] = AMARU_FIXED_REVISION
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )

    identity = prepared.resolution["target_identity"]
    assert identity["source_revision"] == AMARU_FIXED_REVISION
    assert identity["patch_set_sha256"] == AMARU_NANOSECOND_PATCH_SET
    assert identity["measurement_revision"] == "nanoseconds-v2"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"source_revision": "0" * 40}, "source revision"),
        ({"patch_set_sha256": "0" * 64}, "patch-set"),
        (
            {"image": "dwarf/amaru-measurement@sha256:" + "0" * 64},
            "image reference",
        ),
        ({"image_digest": "sha256:" + "0" * 64}, "image"),
        ({"executable_digest": "sha256:" + "0" * 64}, "executable"),
    ],
)
def test_prepare_scenario_measurements_fails_closed_on_patched_identity_mismatch(
    tmp_path, override, message
):
    scenario = load_scenario(
        "dwarf/scenarios/amaru-measurement-overhead-calibration-patched.yaml"
    )
    runtime_path = _patched_runtime(tmp_path / "runtime.json", **override)

    with pytest.raises(MeasurementExecutionError, match=message):
        prepare_scenario_measurements(
            scenario,
            runtime_metadata_path=runtime_path,
            tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
        )


def test_run_factory_uses_run_relative_trace_and_registers_every_resolved_tap(tmp_path):
    scenario = load_scenario("dwarf/scenarios/amaru-measurement-e2e-stock.yaml")
    runtime_path = _runtime(tmp_path / "runtime.json")
    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )
    run_dir = tmp_path / "runs" / "run-1"
    factories = prepared.build_factories(run_dir)

    resolved = {entry["id"] for entry in prepared.resolution["resolved"]}
    assert resolved == set(factories)
    entry = next(
        entry
        for entry in prepared.resolution["resolved"]
        if entry["id"] == "amaru-stock-network"
    )
    collector = factories["amaru-stock-network"](entry)
    assert collector.json_paths == [
        run_dir / "outputs" / "amaru-measurement-run" / "raw" / "amaru-relay-1.ndjson",
    ]
    assert collector.allow_missing_at_start is True


def test_launch_override_is_consumed_by_real_measurement_runtime_resolution(tmp_path):
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id="amaru-measurement-e2e-stock",
            measurement_overrides=[
                {"id": "amaru-stock-network", "enabled": False}
            ],
        )
    )
    launch = create_launch(plan, root=tmp_path / "launches")
    scenario = load_scenario(launch["path"] / "scenario.yaml")

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=_runtime(tmp_path / "runtime.json"),
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )

    assert {item["id"] for item in prepared.resolution["resolved"]} == {
        item["id"] for item in plan["measurements"]["resolved"]
    }
    assert {
        item["id"] for item in prepared.resolution["disabled"]
    } == {"amaru-stock-network"}


def test_scenario_runner_auto_binds_explicit_measurement_profile(
    tmp_path, monkeypatch
):
    source = tmp_path / "scenario.yaml"
    source.write_text(
        json.dumps(
            {
                "spec_version": "v1",
                "id": "amaru-auto-measurement-binding",
                "title": "Amaru automatic measurement binding",
                "target": {"implementation": "amaru", "version": AMARU_VERSION},
                "runtime": "devnet",
                "profile": "profile-r-amaru-measurement-stock-control",
                "measurement_profile": "amaru-security-default",
                "seed": 1,
                "setup": [],
                "load": [],
                "faults": [],
                "probes": [],
                "assertions": [],
                "teardown": [],
            }
        ),
        encoding="utf-8",
    )
    prepared_calls = []
    factory_calls = []
    capture_calls = []
    resolution = {
        "target_identity": {
            "implementation": "amaru",
            "version": AMARU_VERSION,
            "source_revision": AMARU_REVISION,
            "mode": "stock",
            "image_digest": AMARU_DIGEST,
        },
        "profile": {"id": "amaru-security-default"},
        "requested": [],
        "resolved": [],
        "skipped": [],
        "incompatible": [],
        "disabled": [],
    }

    class Prepared:
        def __init__(self):
            self.resolution = resolution
            self.runtime_metadata_path = tmp_path / "runtime.json"

        def build_factories(self, run_dir):
            factory_calls.append(Path(run_dir))
            return {}

    def prepare(scenario):
        prepared_calls.append(scenario.id)
        return Prepared()

    monkeypatch.setattr(
        "profile_manager.measurement_execution.prepare_scenario_measurements",
        prepare,
    )
    monkeypatch.setattr(
        scenario_module.primitives_module,
        "_resolve_client_runtime_target",
        lambda _handle, params: {
            "id": "amaru-relay-1",
            "implementation": "amaru",
            "container": "owned-amaru-relay-1",
            "profile_id": params["profile_id"],
        },
    )

    def capture_window(target, *, started_at, ended_at, destination):
        capture_calls.append(
            {
                "target": target,
                "started_at": started_at,
                "ended_at": ended_at,
                "destination": destination,
            }
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('{"timestamp":"inside"}\n', encoding="utf-8")
        return {
            "container": target["container"],
            "record_count": 1,
            "trace_path": str(destination),
        }

    monkeypatch.setattr(
        scenario_module.primitives_module,
        "_capture_amaru_measurement_window",
        capture_window,
    )

    handle = scenario_module.run_scenario(
        source,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    assert prepared_calls == ["amaru-auto-measurement-binding"]
    assert factory_calls == [handle.run_dir]
    assert len(capture_calls) == 1
    capture = capture_calls[0]
    assert capture["target"]["profile_id"] == "profile-r-amaru-measurement-stock-control"
    assert capture["started_at"] <= capture["ended_at"]
    assert capture["destination"] == (
        handle.run_dir
        / "outputs"
        / "amaru-measurement-run"
        / "raw"
        / "amaru-relay-1.ndjson"
    )
    log_rows = [
        json.loads(line)
        for line in (handle.run_dir / "log.ndjson").read_text().splitlines()
    ]
    events = [row["event"] for row in log_rows]
    assert events.index("node_trace_capture_started") < events.index("phase_started")
    assert events.index("phase_completed") < events.index("node_trace_captured")
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["measurements"]["target_identity"]["version"] == AMARU_VERSION


def test_scenario_trace_capture_refuses_a_setup_changed_runtime_target(
    tmp_path, monkeypatch
):
    source = tmp_path / "scenario.yaml"
    source.write_text(
        json.dumps(
            {
                "spec_version": "v1",
                "id": "amaru-runtime-target-change",
                "title": "Amaru runtime target change",
                "target": {"implementation": "amaru", "version": AMARU_VERSION},
                "runtime": "devnet",
                "profile": "profile-r-amaru-measurement-stock-control",
                "seed": 1,
                "setup": [],
                "load": [],
                "faults": [],
                "probes": [],
                "assertions": [],
                "teardown": [],
            }
        ),
        encoding="utf-8",
    )
    resolution = {
        "target_identity": {
            "implementation": "amaru",
            "version": AMARU_VERSION,
            "source_revision": AMARU_REVISION,
            "mode": "stock",
            "image_digest": AMARU_DIGEST,
        },
        "requested": [],
        "resolved": [],
        "skipped": [],
        "incompatible": [],
        "disabled": [],
    }
    monkeypatch.setattr(
        scenario_module, "prepare_auto_measurements", lambda _scenario: (None, None)
    )
    targets = iter(
        [
            {
                "id": "amaru-relay-1",
                "implementation": "amaru",
                "container": "owned-amaru-relay-before",
                "runtime_metadata_path": "/owned/runtime.json",
            },
            {
                "id": "amaru-relay-1",
                "implementation": "amaru",
                "container": "owned-amaru-relay-after",
                "runtime_metadata_path": "/owned/runtime.json",
            },
        ]
    )
    monkeypatch.setattr(
        scenario_module.primitives_module,
        "_resolve_client_runtime_target",
        lambda *_args, **_kwargs: next(targets),
    )
    monkeypatch.setattr(
        scenario_module.primitives_module,
        "_capture_amaru_measurement_window",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a changed target must never be captured as one window")
        ),
    )

    handle = scenario_module.run_scenario(
        source,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        measurement_context=resolution,
        measurement_collector_factories={},
    )

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    log_rows = [
        json.loads(line)
        for line in (handle.run_dir / "log.ndjson").read_text().splitlines()
    ]
    assert manifest["exit_status"] == "pass"
    assert any(
        row["event"] == "node_trace_unavailable"
        and row["payload"]["stage"] == "finish"
        and "target changed" in row["payload"]["reason"]
        for row in log_rows
    )


@pytest.mark.parametrize("failure_stage", ["start", "finish"])
def test_scenario_trace_capture_failure_preserves_terminal_measurement_evidence(
    tmp_path, monkeypatch, failure_stage
):
    source = tmp_path / "scenario.yaml"
    source.write_text(
        json.dumps(
            {
                "spec_version": "v1",
                "id": "amaru-shared-trace-failure",
                "title": "Amaru shared trace failure",
                "target": {"implementation": "amaru", "version": AMARU_VERSION},
                "runtime": "devnet",
                "profile": "profile-r-amaru-measurement-stock-control",
                "attach": {"topology": "cardano_amaru"},
                "seed": 1,
                "setup": [
                    {
                        "primitive": "runtime_attach_topology",
                        "topology": "cardano_amaru",
                    }
                ],
                "load": [],
                "faults": [],
                "probes": [],
                "assertions": [],
                "teardown": [],
            }
        ),
        encoding="utf-8",
    )
    resolution = {
        "target_identity": {
            "implementation": "amaru",
            "version": AMARU_VERSION,
            "source_revision": AMARU_REVISION,
            "mode": "stock",
            "image_digest": AMARU_DIGEST,
        },
        "profile": {"id": "amaru-security-default"},
        "requested": [],
        "resolved": [],
        "skipped": [],
        "incompatible": [],
        "disabled": [],
    }
    monkeypatch.setattr(
        scenario_module,
        "prepare_auto_measurements",
        lambda _scenario: (None, None),
    )

    class FakeLock:
        closed = False

        def close(self):
            self.closed = True

    lock = FakeLock()
    monkeypatch.setattr(
        scenario_module, "acquire_topology_lock", lambda *_args, **_kwargs: lock
    )
    monkeypatch.setattr(
        scenario_module, "_run_phase", lambda *_args, **_kwargs: None
    )

    def resolve_target(*_args, **_kwargs):
        if failure_stage == "start":
            raise RuntimeError("runtime metadata is unavailable")
        return {
            "id": "amaru-relay-1",
            "implementation": "amaru",
            "container": "owned-amaru-relay-1",
        }

    monkeypatch.setattr(
        scenario_module.primitives_module,
        "_resolve_client_runtime_target",
        resolve_target,
    )
    monkeypatch.setattr(
        scenario_module.primitives_module,
        "_capture_amaru_measurement_window",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("cannot retain the measured Amaru node trace window")
        ),
    )

    handle = scenario_module.run_scenario(
        source,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        topology_preflight=lambda *_args: {
            "state": "healthy",
            "reason_code": "all_mixed_readiness_gates_passed",
        },
        measurement_context=resolution,
        measurement_collector_factories={},
    )

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    runtime = json.loads((handle.run_dir / "measurements/runtime.json").read_text())
    log_rows = [
        json.loads(line)
        for line in (handle.run_dir / "log.ndjson").read_text().splitlines()
    ]
    assert manifest["exit_status"] == "pass"
    assert runtime["collector_states"] == {}
    assert lock.closed is True
    assert any(
        row["event"] == "node_trace_unavailable"
        and row["level"] == "warning"
        and row["payload"]["stage"] == failure_stage
        for row in log_rows
    )
