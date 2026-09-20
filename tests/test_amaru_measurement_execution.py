import json
from pathlib import Path

import pytest

from profile_manager.measurement_execution import (
    MeasurementExecutionError,
    prepare_scenario_measurements,
)
from profile_manager.scenario import load_scenario
from profile_manager import scenario as scenario_module


AMARU_VERSION = "10.11.20260912"
AMARU_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
AMARU_DIGEST = "sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e"
AMARU_PATCHED_DIGEST = "sha256:dacb2351e69ab1d0bbfbc569b222d1bde40d9a158e79c556b30df554375addcc"
AMARU_PATCHED_EXECUTABLE = "sha256:b0cebe917e092c3d4e47ac4841de7a49338dca1e586616803fe17e92d0d93425"
AMARU_PATCH_SET = "f0e1aebca9adf2713d4d9f6f8ba33f20b0d04c3b35de6127d4a1e027a68b50af"


def _runtime(path: Path, *, matched=True, digest=AMARU_DIGEST):
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
                    "artifact_image_id": digest,
                    "matched": matched,
                }
            },
        },
        "measurement_target": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _patched_runtime(path: Path, **overrides):
    measurement_target = {
        "implementation": "amaru",
        "target_mode": "patched",
        "version": AMARU_VERSION,
        "source_revision": AMARU_REVISION,
        "patch_set_sha256": AMARU_PATCH_SET,
        "image": f"dwarf/amaru-measurement@{AMARU_PATCHED_DIGEST}",
        "image_digest": AMARU_PATCHED_DIGEST,
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
                    "artifact_image_id": AMARU_PATCHED_DIGEST,
                    "executable_digest_reported": AMARU_PATCHED_EXECUTABLE,
                    "executable_digest_matched": True,
                }
            },
        },
        "measurement_target": measurement_target,
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
    with pytest.raises(MeasurementExecutionError, match="image digest"):
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
        "image_reference": f"dwarf/amaru-measurement@{AMARU_PATCHED_DIGEST}",
        "image_digest": AMARU_PATCHED_DIGEST,
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


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"source_revision": "0" * 40}, "source revision"),
        ({"patch_set_sha256": "0" * 64}, "patch-set"),
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
        run_dir / "outputs" / "amaru-measurement-calibration" / "raw" / "amaru-relay-1.ndjson",
        run_dir / "outputs" / "protocol-decode-cases" / "raw" / "amaru-relay-1.ndjson",
    ]
    assert collector.allow_missing_at_start is True


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

    handle = scenario_module.run_scenario(
        source,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    assert prepared_calls == ["amaru-auto-measurement-binding"]
    assert factory_calls == [handle.run_dir]
    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["measurements"]["target_identity"]["version"] == AMARU_VERSION
