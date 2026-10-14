import hashlib
import json
import tarfile

import pytest
from jsonschema import Draft202012Validator

from profile_manager import scenario, telemetry
from profile_manager.measurement_runtime import (
    MeasurementRuntime,
)


def _resolved(measurement_id, *, gate=None):
    return {
        "id": measurement_id,
        "enabled": True,
        "source": "scenario-override",
        "parameters": {"window_seconds": 5},
        "threshold_gate": gate or {"enabled": False, "thresholds": []},
        "definition_digest": "sha256:" + "d" * 64,
        "definition": {
            "schema_version": "v1",
            "id": measurement_id,
            "collection_mode": "external",
            "emitted_artifacts": [f"measurements/{measurement_id}/result.json"],
        },
    }


def _resolution(*entries):
    return {
        "target_identity": {
            "implementation": "amaru",
            "version": "10.11.20260912",
            "source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
            "mode": "stock",
            "image_digest": "sha256:" + "4" * 64,
        },
        "profile": {"id": "test", "source": "explicit-profile"},
        "requested": [
            {"id": entry["id"], "enabled": True, "source": entry["source"]}
            for entry in entries
        ],
        "resolved": list(entries),
        "skipped": [],
        "incompatible": [],
        "disabled": [],
    }


class RecordingCollector:
    def __init__(self, events, *, result=None, fail_at=None):
        self.events = events
        self.result = result or {"metrics": []}
        self.fail_at = fail_at

    def _record(self, name, value=None):
        self.events.append((name, value))
        if self.fail_at == name:
            raise RuntimeError(f"failed during {name}")

    def prepare(self, context):
        self._record("prepare", context.measurement_id)
        context.write_json("prepared.json", {"prepared": True})

    def start(self, context):
        self._record("start", context.measurement_id)

    def on_marker(self, marker, context):
        self._record("marker", (marker["phase_id"], marker["state"]))

    def stop(self, context):
        self._record("stop", context.measurement_id)

    def finalize(self, context):
        self._record("finalize", context.measurement_id)
        return self.result


def test_runtime_executes_lifecycle_and_retains_phase_windows(tmp_path):
    events = []
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(events),
        },
        wall_clock=iter([100.0, 101.0, 111.0, 112.0]).__next__,
        monotonic_clock=iter([10.0, 11.0, 21.0, 22.0]).__next__,
    )

    runtime.prepare()
    runtime.start()
    runtime.mark_phase("hostile", "start", phase_index=1)
    runtime.mark_phase("hostile", "end", phase_index=1)
    result = runtime.finalize()

    assert events == [
        ("prepare", "tap-a"),
        ("start", "tap-a"),
        ("marker", ("hostile", "start")),
        ("marker", ("hostile", "end")),
        ("marker", ("run", "end")),
        ("stop", "tap-a"),
        ("finalize", "tap-a"),
    ]
    assert result["collector_states"] == {"tap-a": "finalized"}
    assert result["gate_failed"] is False
    assert json.loads(
        (tmp_path / "measurements/collectors/tap-a/prepared.json").read_text()
    ) == {"prepared": True}
    markers = [
        json.loads(line)
        for line in (tmp_path / "measurements/windows.ndjson").read_text().splitlines()
    ]
    assert [(item["phase_id"], item["state"]) for item in markers] == [
        ("run", "start"),
        ("hostile", "start"),
        ("hostile", "end"),
        ("run", "end"),
    ]
    assert markers[-1]["elapsed_seconds"] == 12.0


def test_runtime_elapsed_seconds_uses_the_run_monotonic_origin(tmp_path):
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(),
        collector_factories={},
        wall_clock=iter([100.0]).__next__,
        monotonic_clock=iter([5000.0, 5002.184]).__next__,
    )

    runtime.prepare()

    assert runtime.elapsed_seconds() == 2.184


@pytest.mark.parametrize("path", ["../escape.json", "/tmp/escape.json", "a/../../escape"])
def test_collector_artifacts_cannot_escape_their_bounded_directory(tmp_path, path):
    class EscapingCollector(RecordingCollector):
        def prepare(self, context):
            context.write_json(path, {"bad": True})

    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: EscapingCollector([]),
        },
    )

    runtime.prepare()

    assert runtime.snapshot()["collector_states"] == {"tap-a": "error"}
    assert runtime.snapshot()["collector_errors"][0]["type"] == "MeasurementArtifactPathError"
    assert not (tmp_path.parent / "escape.json").exists()


def test_collector_failure_is_isolated_and_other_collectors_finalize(tmp_path):
    bad_events = []
    good_events = []
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("bad"), _resolved("good")),
        collector_factories={
            "bad": lambda _entry: RecordingCollector(bad_events, fail_at="start"),
            "good": lambda _entry: RecordingCollector(good_events),
        },
    )

    runtime.prepare()
    runtime.start()
    runtime.mark_phase("load", "start")
    result = runtime.finalize()

    assert result["collector_states"] == {"bad": "error", "good": "finalized"}
    assert result["collector_errors"][0]["measurement_id"] == "bad"
    assert ("finalize", "good") in good_events
    assert ("marker", ("load", "start")) in good_events


def test_only_explicit_threshold_gate_can_change_verdict_not_operational_status(tmp_path):
    metric = {
        "metrics": [
            {"name": "accepted_per_second", "value": 7.0, "unit": "tx/s"}
        ]
    }
    ungated = MeasurementRuntime(
        run_dir=tmp_path / "ungated",
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result=metric),
        },
    )
    ungated.prepare()
    ungated.start()
    ungated_result = ungated.finalize()

    gate = {
        "enabled": True,
        "thresholds": [
            {
                "metric": "accepted_per_second",
                "operator": "gte",
                "value": 10,
                "unit": "tx/s",
            }
        ],
    }
    gated = MeasurementRuntime(
        run_dir=tmp_path / "gated",
        resolution=_resolution(_resolved("tap-a", gate=gate)),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result=metric),
        },
    )
    gated.prepare()
    gated.start()
    gated_result = gated.finalize()

    assert ungated_result["gate_failed"] is False
    assert ungated.scenario_exit_status("pass") == "pass"
    assert gated_result["gate_failed"] is True
    assert gated_result["threshold_gates"]["tap-a"]["result"] == "fail"
    assert gated.scenario_exit_status("pass") == "pass"
    assert gated.scenario_verdict(
        "pass", {"total": 1, "pass": 1, "fail": 0}
    ) == "fail"
    assert gated.scenario_exit_status("error") == "error"


def test_threshold_gate_reads_normalized_measurements_mapping(tmp_path):
    gate = {
        "enabled": True,
        "thresholds": [
            {
                "metric": "successful_operations_per_second",
                "operator": "gte",
                "value": 5,
                "unit": "operations/s",
            }
        ],
    }
    result = {
        "measurements": {
            "successful_operations_per_second": {
                "status": "available",
                "value": 7.0,
                "unit": "operations/s",
            }
        }
    }
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a", gate=gate)),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result=result),
        },
    )
    runtime.prepare()
    runtime.start()

    finalized = runtime.finalize()

    assert finalized["threshold_gates"]["tap-a"]["result"] == "pass"
    assert finalized["gate_failed"] is False


def test_explicit_gate_fails_closed_when_collector_is_unavailable(tmp_path):
    gate = {
        "enabled": True,
        "thresholds": [
            {"metric": "accepted_per_second", "operator": "gte", "value": 1}
        ],
    }
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("missing-tap", gate=gate)),
        collector_factories={},
    )

    runtime.prepare()
    runtime.start()
    result = runtime.finalize()

    assert result["collector_states"] == {"missing-tap": "error"}
    assert result["threshold_gates"]["missing-tap"]["result"] == "fail"
    assert result["gate_failed"] is True
    assert runtime.scenario_exit_status("pass") == "pass"
    assert runtime.scenario_verdict(
        "pass", {"total": 1, "pass": 1, "fail": 0}
    ) == "fail"


def test_finalize_writes_collector_measurements_into_existing_run_bundle(tmp_path):
    collector_result = {
        "measurements": {
            "transfer": {
                "status": "available",
                "unit": "us",
                "sample_count": 20,
                "mean": 52.0,
                "median": 51.0,
                "p95": 70.0,
                "p99": 90.0,
            }
        }
    }
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result=collector_result),
        },
        scenario_id="amaru-transfer-test",
    )
    runtime.prepare()
    runtime.start()
    result = runtime.finalize()

    assert result["report_artifacts"] == {
        "summary": "measurements/summary.json",
        "report": "measurements/report.json",
        "readable": "measurements/report.md",
        "compact_table": "measurements/compact-table.json",
    }
    report = json.loads((tmp_path / "measurements/report.json").read_text())
    assert report["scenario"] == "amaru-transfer-test"
    assert report["measurements"]["transfer"]["p99"] == 90.0


def test_finalize_writes_schema_valid_per_tap_result_and_exports_it(tmp_path):
    class EvidenceCollector(RecordingCollector):
        def finalize(self, context):
            context.write_json("source.json", {"records": 20})
            return super().finalize(context)

    collector_result = {
        "measurements": {
            "transfer": {
                "status": "available",
                "unit": "us",
                "sample_count": 20,
                "mean": 52.0,
                "p95": 70.0,
            }
        },
        "artifacts": {"source": "measurements/collectors/tap-a/source.json"},
    }
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: EvidenceCollector([], result=collector_result),
        },
        scenario_id="amaru-transfer-test",
    )
    runtime.prepare()
    runtime.start()
    runtime.finalize()

    result_path = tmp_path / "measurements/tap-a/result.json"
    result_bytes = result_path.read_bytes()
    result = json.loads(result_bytes)
    schema = json.loads(
        open("dwarf/spec/v1/measurement-result.schema.json", encoding="utf-8").read()
    )
    Draft202012Validator(schema).validate(result)
    assert result["measurement_id"] == "tap-a"
    assert result["run_id"] == tmp_path.name
    assert result["status"] == "collected"
    assert result["sample_count"] == 20
    assert result["target"] == _resolution()["target_identity"]
    assert result["provenance"]["scenario_id"] == "amaru-transfer-test"
    assert result["records"] == [
        {
            "name": "transfer",
            "status": "available",
            "unit": "us",
            "sample_count": 20,
            "mean": 52.0,
            "p95": 70.0,
        }
    ]
    assert result["artifacts"] == [
        "measurements/collectors/tap-a/prepared.json",
        "measurements/collectors/tap-a/source.json",
        "measurements/tap-a/result.json",
    ]

    from profile_manager.forensic import export_bundle

    bundle = export_bundle(
        tmp_path.name,
        runs_dir=tmp_path.parent,
        bundles_dir=tmp_path.parent / "bundles",
    )
    with tarfile.open(bundle, "r:gz") as archive:
        member = archive.extractfile(f"{tmp_path.name}/measurements/tap-a/result.json")
        assert member is not None
        bundled_bytes = member.read()
    assert hashlib.sha256(bundled_bytes).hexdigest() == hashlib.sha256(
        result_bytes
    ).hexdigest()


def test_measurement_result_schema_scopes_nullable_source_to_complete_local_identity():
    schema = json.loads(
        open("dwarf/spec/v1/measurement-result.schema.json", encoding="utf-8").read()
    )
    validator = Draft202012Validator(schema)
    base = {
        "schema_version": "v1",
        "measurement_id": "tap-a",
        "run_id": "run-a",
        "status": "unavailable",
        "result_kind": "summary",
        "target": {
            "implementation": "amaru",
            "version": "developer-build",
            "source_revision": None,
            "mode": "stock",
        },
        "provenance": {
            "scenario_id": "scenario-a",
            "collector_mode": "external",
            "hardware": {},
            "dataset": {},
        },
        "artifacts": [],
    }

    assert list(validator.iter_errors(base))
    incomplete_local = json.loads(json.dumps(base))
    incomplete_local["target"]["identity_kind"] = "operator-local-config"
    assert list(validator.iter_errors(incomplete_local))

    complete_local = json.loads(json.dumps(incomplete_local))
    complete_local["target"].update(
        {
            "image_reference": "sha256:" + "1" * 64,
            "image_config_id": "sha256:" + "1" * 64,
            "executable_digest": "sha256:" + "2" * 64,
            "platform": "linux/amd64",
            "operator_registration_id": "registration-0123456789abcdef01234567",
            "operator_overlay_revision": "sha256:" + "3" * 64,
            "controller_installation_id": "install-test",
            "version_catalog_revision": "4" * 64,
        }
    )
    validator.validate(complete_local)

    for field, malformed in (
        ("image_reference", "local-image:mutable"),
        ("image_config_id", "sha256:short"),
        ("executable_digest", ""),
        ("operator_registration_id", "registration/unsafe"),
        ("operator_overlay_revision", "3" * 64),
        ("controller_installation_id", "install/unsafe"),
        ("version_catalog_revision", "latest"),
    ):
        candidate = json.loads(json.dumps(complete_local))
        candidate["target"][field] = malformed
        assert list(validator.iter_errors(candidate)), field


def test_finalize_writes_honest_unavailable_and_failed_results(tmp_path):
    no_signal = _resolved("no-signal")
    missing = _resolved("missing")
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(no_signal, missing),
        collector_factories={
            "no-signal": lambda _entry: RecordingCollector(
                [],
                result={
                    "measurements": {
                        "latency": {
                            "status": "unavailable",
                            "reason": "no matching spans",
                            "sample_count": 0,
                        }
                    }
                },
            )
        },
        scenario_id="honest-results",
    )
    runtime.prepare()
    runtime.start()
    runtime.finalize()

    unavailable = json.loads(
        (tmp_path / "measurements/no-signal/result.json").read_text()
    )
    failed = json.loads((tmp_path / "measurements/missing/result.json").read_text())
    assert unavailable["status"] == "unavailable"
    assert unavailable["sample_count"] == 0
    assert unavailable["unavailable_reason"] == "no matching spans"
    assert failed["status"] == "failed"
    assert "no collector factory is registered" in failed["unavailable_reason"]


@pytest.mark.parametrize("implementation", ["amaru", "cardano-node"])
def test_per_tap_result_preserves_qualified_target_provenance_for_each_client(
    tmp_path, implementation
):
    target = {
        "implementation": implementation,
        "version": "10.11.20260918",
        "source_revision": "a" * 40,
        "mode": "patched",
        "image_digest": "sha256:" + "1" * 64,
        "image_reference": "sha256:" + "1" * 64,
        "image_manifest_digest": "sha256:" + "2" * 64,
        "image_config_digest": "sha256:" + "1" * 64,
        "qualification_image_id": "sha256:" + "3" * 64,
        "source_image_reference": "registry.example/amaru@sha256:" + "2" * 64,
        "transfer_manifest_sha256": "sha256:" + "4" * 64,
        "transfer_source_installation_id": "legacy-qualified-source",
        "executable_digest": "sha256:" + "5" * 64,
        "build_result_sha256": "sha256:" + "6" * 64,
        "runtime_probe_log_sha256": "sha256:" + "7" * 64,
        "patch_set_sha256": "8" * 64,
        "measurement_revision": "amaru-20260918-measurements-v1",
        "version_catalog_revision": "9" * 64,
        "profile_id": "profile-z",
        "deployment_adapter": "relay-bootstrap",
        "qualification_status": "confirmed",
    }
    entry = _resolved("tap-a")
    entry["definition"]["compatibility"] = {"implementation": implementation}
    resolution = _resolution(entry)
    resolution["target_identity"] = target
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=resolution,
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(
                [],
                result={
                    "measurements": {
                        "signal": {"status": "available", "value": 1}
                    }
                },
            )
        },
        scenario_id="mixed-qualified-boundary",
    )
    runtime.prepare()
    runtime.start()
    runtime.finalize()

    result = json.loads((tmp_path / "measurements/tap-a/result.json").read_text())
    assert result["target"] == target
    assert result["provenance"]["scenario_id"] == "mixed-qualified-boundary"


def test_per_tap_result_rejects_wrong_client_attribution(tmp_path):
    entry = _resolved("tap-a")
    entry["definition"]["compatibility"] = {"implementation": "cardano-node"}
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(entry),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([]),
        },
    )
    runtime.prepare()
    runtime.start()

    result = runtime.finalize()

    assert result["collector_states"] == {"tap-a": "error"}
    assert result["result_artifacts"] == {}
    assert result["collector_errors"][-1]["phase"] == "result-artifact"
    assert "targets cardano-node" in result["collector_errors"][-1]["message"]
    assert not (tmp_path / "measurements/tap-a/result.json").exists()


@pytest.mark.parametrize(
    "entries, error",
    [
        (
            [
                {
                    **_resolved("tap-a"),
                    "definition": {
                        **_resolved("tap-a")["definition"],
                        "emitted_artifacts": ["measurements/../escape/result.json"],
                    },
                }
            ],
            "escapes the run bundle",
        ),
        (
            [
                _resolved("tap-a"),
                {
                    **_resolved("tap-b"),
                    "definition": {
                        **_resolved("tap-b")["definition"],
                        "emitted_artifacts": ["measurements/tap-a/result.json"],
                    },
                },
            ],
            "belongs to tap-a",
        ),
    ],
)
def test_per_tap_result_paths_fail_closed(entries, error, tmp_path):
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(*entries),
        collector_factories={
            entry["id"]: (lambda _entry: RecordingCollector([]))
            for entry in entries
        },
    )
    runtime.prepare()
    runtime.start()

    result = runtime.finalize()

    artifact_errors = [
        item for item in result["collector_errors"] if item["phase"] == "result-artifact"
    ]
    assert artifact_errors
    assert any(error in item["message"] for item in artifact_errors)
    assert (tmp_path / "measurements/runtime.json").is_file()


def test_per_tap_result_refuses_in_run_symlink_alias_without_overwrite(tmp_path):
    sentinel = tmp_path / "manifest.json"
    sentinel.write_text('{"sentinel": true}\n', encoding="utf-8")
    result_dir = tmp_path / "measurements/tap-a"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").symlink_to("../../manifest.json")
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([]),
        },
    )
    runtime.prepare()
    runtime.start()

    result = runtime.finalize()

    assert sentinel.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert (result_dir / "result.json").is_symlink()
    assert result["collector_states"] == {"tap-a": "error"}
    artifact_error = result["collector_errors"][-1]
    assert artifact_error["phase"] == "result-artifact"
    assert "contains a symlink" in artifact_error["message"]


def test_per_tap_result_counts_only_available_nested_samples(tmp_path):
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(
                [],
                result={
                    "measurements": {
                        "distribution": {
                            "status": "available",
                            "value": {"sample_count": 37, "p95": 12.0},
                        },
                        "missing": {
                            "status": "unavailable",
                            "value": {"sample_count": 99},
                            "reason": "no matching spans",
                        },
                        "zero_counter": {"status": "available", "value": 0},
                    }
                },
            ),
        },
    )
    runtime.prepare()
    runtime.start()
    runtime.finalize()

    result = json.loads((tmp_path / "measurements/tap-a/result.json").read_text())
    assert result["status"] == "collected"
    assert result["sample_count"] == 37
    assert result["warnings"] == []


@pytest.mark.parametrize(
    "claim",
    [
        {"status": "collected"},
        {"coverage": {}},
        {"coverage": {"claimed_lines": 12}},
    ],
)
def test_per_tap_result_does_not_trust_bare_evidence_claims(tmp_path, claim):
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result=claim),
        },
    )
    runtime.prepare()
    runtime.start()
    runtime.finalize()

    result = json.loads((tmp_path / "measurements/tap-a/result.json").read_text())
    assert result["status"] == "unavailable"
    assert result["sample_count"] == 0
    assert result["records"] == []
    assert result["warnings"] == []
    assert result["unavailable_reason"] == (
        "collector produced no available measurement evidence"
    )


def test_experimental_local_tap_retains_exact_consent_and_invalid_output_is_unavailable(
    tmp_path,
):
    entry = _resolved("tap-a")
    entry.update(
        {
            "experimental_consent": True,
            "compatibility": {"state": "unknown", "reason": "source is unverified"},
            "capability": {"state": "available", "reason": "external collector"},
        }
    )
    resolution = _resolution(entry)
    resolution["target_identity"] = {
        "implementation": "amaru",
        "version": "local-amaru",
        "source_revision": None,
        "mode": "stock",
        "identity_kind": "operator-local-config",
        "image_reference": "sha256:" + "2" * 64,
        "image_digest": None,
        "image_config_id": "sha256:" + "2" * 64,
        "executable_digest": "sha256:" + "3" * 64,
        "platform": "linux/amd64",
        "operator_registration_id": "registration-local-amaru",
        "operator_overlay_revision": "sha256:" + "4" * 64,
        "controller_installation_id": "install-test",
        "version_catalog_revision": "5" * 64,
    }
    resolution["experimental_consent"] = {
        "tap_ids": ["tap-a"],
        "context_digest": "sha256:" + "6" * 64,
        "target_identity": resolution["target_identity"],
        "definition_digests": {"tap-a": entry["definition_digest"]},
    }
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=resolution,
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector([], result={})
        },
        scenario_id="experimental-local-output",
    )
    runtime.prepare()
    runtime.start()
    runtime.finalize()

    result = json.loads((tmp_path / "measurements/tap-a/result.json").read_text())
    assert result["status"] == "unavailable"
    assert result["sample_count"] == 0
    assert result["target"]["source_revision"] is None
    assert result["target"]["image_config_id"] == "sha256:" + "2" * 64
    assert result["target"]["operator_registration_id"] == "registration-local-amaru"
    assert result["provenance"]["experimental_consent"] is True
    assert result["provenance"]["experimental_context_digest"] == "sha256:" + "6" * 64
    assert result["provenance"]["compatibility"]["state"] == "unknown"
    assert result["provenance"]["capability"]["state"] == "available"


def test_per_tap_result_rejects_foreign_declared_artifact(tmp_path):
    sentinel = tmp_path / "manifest.json"
    sentinel.write_text('{"sentinel": true}\n', encoding="utf-8")
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(
                [],
                result={
                    "measurements": {
                        "signal": {"status": "available", "value": 1}
                    },
                    "artifacts": {"foreign": "manifest.json"},
                },
            ),
        },
    )
    runtime.prepare()
    runtime.start()

    result = runtime.finalize()

    assert sentinel.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert result["collector_states"] == {"tap-a": "error"}
    assert "belongs outside its evidence root" in result["collector_errors"][-1]["message"]
    assert not (tmp_path / "measurements/tap-a/result.json").exists()


def test_per_tap_result_rejects_symlinked_collector_artifact(tmp_path):
    sentinel = tmp_path / "manifest.json"
    sentinel.write_text('{"sentinel": true}\n', encoding="utf-8")
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(
                [],
                result={
                    "measurements": {
                        "signal": {"status": "available", "value": 1}
                    }
                },
            ),
        },
    )
    runtime.prepare()
    alias = tmp_path / "measurements/collectors/tap-a/foreign.json"
    alias.symlink_to("../../../manifest.json")
    runtime.start()

    result = runtime.finalize()

    assert sentinel.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert alias.is_symlink()
    assert result["collector_states"] == {"tap-a": "error"}
    assert "contains a symlink" in result["collector_errors"][-1]["message"]
    assert not (tmp_path / "measurements/tap-a/result.json").exists()


def test_non_json_collector_result_is_bounded_and_retained_as_failed_tap(tmp_path):
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(
                [],
                result={
                    "measurements": {
                        "bad": {"status": "available", "value": object()}
                    }
                },
            ),
        },
    )
    runtime.prepare()
    runtime.start()

    result = runtime.finalize()

    error = result["collector_errors"][0]
    assert error["phase"] == "finalize"
    assert "not JSON serializable" in error["message"]
    assert len(error["message"]) <= 512
    assert result["collector_states"] == {"tap-a": "error"}
    assert json.loads(
        (tmp_path / "measurements/tap-a/result.json").read_text()
    )["status"] == "failed"
    assert (tmp_path / "measurements/runtime.json").is_file()


def test_finalize_stops_collectors_after_runner_exception_and_is_idempotent(tmp_path):
    events = []
    runtime = MeasurementRuntime(
        run_dir=tmp_path,
        resolution=_resolution(_resolved("tap-a")),
        collector_factories={
            "tap-a": lambda _entry: RecordingCollector(events),
        },
    )
    runtime.prepare()
    runtime.start()

    try:
        raise RuntimeError("scenario runner failed")
    except RuntimeError:
        first = runtime.finalize()
    second = runtime.finalize()

    assert events.count(("stop", "tap-a")) == 1
    assert events.count(("finalize", "tap-a")) == 1
    assert second == first


class _NoopObserver:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def summarize(self):
        return {}


def test_scenario_runner_wraps_actual_phases_and_finalizes_after_exception(
    monkeypatch, tmp_path
):
    events = []
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        json.dumps(
            {
                "spec_version": "v1",
                "id": "measured-runner",
                "title": "Measured runner",
                "target": {
                    "implementation": "amaru",
                    "version": "10.11.20260912",
                },
                "runtime": "library",
                "setup": [],
                "load": [{"primitive": "load_shell_command", "command": "true"}],
                "faults": [],
                "probes": [],
                "assertions": [],
                "teardown": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(telemetry, "ObserverCollector", _NoopObserver)

    def fail_load(_handle, _rng, _registry, _scen, refs, phase, **_kwargs):
        if refs and phase.endswith(":load"):
            raise RuntimeError("load failed")

    monkeypatch.setattr(scenario, "_run_phase", fail_load)

    handle = scenario.run_scenario(
        scenario_path,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        measurement_context=_resolution(_resolved("tap-a")),
        measurement_collector_factories={
            "tap-a": lambda _entry: RecordingCollector(events),
        },
    )

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    assert manifest["exit_status"] == "error"
    assert manifest["measurements"]["collector_states"] == {"tap-a": "finalized"}
    assert ("stop", "tap-a") in events
    assert ("finalize", "tap-a") in events
    markers = [
        json.loads(line)
        for line in (handle.run_dir / "measurements/windows.ndjson").read_text().splitlines()
    ]
    assert [(item["phase_id"], item["state"]) for item in markers] == [
        ("run", "start"),
        ("default", "start"),
        ("default", "end"),
        ("run", "end"),
    ]
    from profile_manager import forensic

    verification = forensic.verify(
        handle.run_id,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    assert verification.ok, verification.errors
    bundle = forensic.export_bundle(
        handle.run_id,
        runs_dir=tmp_path / "runs",
        bundles_dir=tmp_path / "bundles",
    )
    with tarfile.open(bundle, "r:gz") as archive:
        assert (
            f"{handle.run_id}/measurements/tap-a/result.json"
            in archive.getnames()
        )


def test_result_write_failure_preserves_terminal_run_and_lock_cleanup(
    monkeypatch, tmp_path
):
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        json.dumps(
            {
                "spec_version": "v1",
                "id": "measurement-artifact-failure",
                "title": "Measurement artifact failure",
                "target": {
                    "implementation": "amaru",
                    "version": "10.11.20260912",
                },
                "runtime": "devnet",
                "attach": {"topology": "cardano_amaru"},
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

    class FakeLock:
        closed = False

        def close(self):
            self.closed = True

    lock = FakeLock()
    monkeypatch.setattr(telemetry, "ObserverCollector", _NoopObserver)
    monkeypatch.setattr(scenario, "_run_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scenario, "acquire_topology_lock", lambda *_args, **_kwargs: lock)

    def fail_per_tap_write(path, payload):
        assert payload
        if path.name == "result.json":
            raise OSError("bounded per-tap write failure")
        raise AssertionError(f"unexpected atomic write destination: {path}")

    monkeypatch.setattr(
        "profile_manager.measurement_runtime.atomic_write_bytes", fail_per_tap_write
    )

    handle = scenario.run_scenario(
        scenario_path,
        runs_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        topology_preflight=lambda *_args: {
            "state": "healthy",
            "reason_code": "all_mixed_readiness_gates_passed",
        },
        measurement_context=_resolution(_resolved("tap-a")),
        measurement_collector_factories={
            "tap-a": lambda _entry: RecordingCollector(
                [],
                result={
                    "measurements": {
                        "signal": {"status": "available", "value": 1}
                    }
                },
            ),
        },
    )

    manifest = json.loads((handle.run_dir / "manifest.json").read_text())
    runtime = json.loads((handle.run_dir / "measurements/runtime.json").read_text())
    assert manifest["exit_status"] == "pass"
    assert manifest["measurements"]["collector_states"] == {"tap-a": "error"}
    assert runtime["collector_errors"][-1]["phase"] == "result-artifact"
    assert runtime["collector_errors"][-1]["message"] == "bounded per-tap write failure"
    assert not (handle.run_dir / "measurements/tap-a/result.json").exists()
    assert lock.closed is True
