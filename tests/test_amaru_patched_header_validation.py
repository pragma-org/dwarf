"""Contract tests only: synthetic traces and patch application never prove a node run."""
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from profile_manager.measurement_collectors import amaru_patched as patched
from profile_manager.measurement_runtime import CollectorContext
from profile_manager.measurement_targets import MeasurementTargetError, resolve_patched_amaru_target
from scripts import build_amaru_measurement_target as builder

REVISION = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
TAP = "amaru-patched-header-validation"
ROOT = Path("dwarf/targets/amaru/measurement-patches-nanoseconds-v4") / REVISION
PREIMAGE = Path("tests/fixtures/amaru-aedf-validate-header.rs")


def manifest():
    return json.loads((ROOT / "manifest.json").read_text())


def identity():
    return {"implementation": "amaru", "version": "10.11.20260918", "mode": "patched",
            "source_revision": REVISION, "patch_set_sha256": manifest()["patch_set_sha256"],
            "image_digest": "sha256:" + "1" * 64,
            "executable_digest": "sha256:" + "2" * 64,
            "build_result_sha256": "sha256:" + "3" * 64,
            "runtime_probe_log_sha256": "sha256:" + "4" * 64}


def event(outcome="accepted", **fields):
    return {"target": "amaru::consensus::measurement", "fields": {
        "message": "measurement.header_validation", "boundary": "consensus-validate-header",
        "header_hash": "a" * 64, "outcome": outcome,
        "error_kind": {"accepted": "none", "rejected": "praos-assertion",
                       "unavailable": "nonces"}.get(outcome if isinstance(outcome, str) else "invalid", "none"),
        "elapsed_nanos": 1234, "elapsed_micros": 1, **fields}}


def collect(tmp_path, records):
    trace = tmp_path / "trace.ndjson"
    trace.write_text("".join(json.dumps(record) + "\n" for record in records))
    context = CollectorContext(run_dir=tmp_path, collector_dir=tmp_path / "measurements" / TAP,
                               measurement_id=TAP, definition={}, parameters={})
    collector = patched.build_amaru_patched_factories(
        json_trace_paths=[trace], target_identity=identity(), include_existing=True)[TAP]({"id": TAP})
    collector.prepare(context)
    collector.start(context)
    return collector.finalize(context)


def test_exact_patch_manifest_and_preimage_apply_without_build(tmp_path):
    m = manifest()
    assert m["source"]["revision"] == REVISION
    assert m["toolchain"] == "nightly-2026-09-04"
    assert m["build_tools"] == {"cargo-zigbuild": "0.23.4", "target": "x86_64-unknown-linux-musl", "zig": "0.15.2"}
    assert m["measurement_revision"] == "nanoseconds-v4"
    assert builder.verify_patch_set(ROOT, m)["patch_set_sha256"] == m["patch_set_sha256"]
    previous = json.loads((Path(str(ROOT).replace("nanoseconds-v4", "nanoseconds-v3")) / "manifest.json").read_text())
    assert m["patches"][0] == previous["patches"][0]
    patch = m["patches"][1]
    source_path = "crates/amaru-consensus/src/validate_header.rs"
    assert patch["preimages"] == [{"path": source_path, "sha256": hashlib.sha256(PREIMAGE.read_bytes()).hexdigest()}]
    source = tmp_path / "source"
    (source / source_path).parent.mkdir(parents=True)
    (source / source_path).write_bytes(PREIMAGE.read_bytes())
    builder.verify_preimages(source, patch["preimages"])
    subprocess.run(["git", "apply", "--check", "--whitespace=error-all", str((ROOT / patch["path"]).resolve())], cwd=source, check=True)
    subprocess.run(["git", "apply", "--whitespace=error-all", str((ROOT / patch["path"]).resolve())], cwd=source, check=True)
    text = (source / source_path).read_text()
    assert "let result = validate_header_inner(" in text
    assert 'Err(ValidateHeaderError::Assert(_)) => ("rejected", "praos-assertion")' in text
    assert 'Err(ValidateHeaderError::Nonces(_)) => ("unavailable", "nonces")' in text
    assert 'elapsed_nanos / 1_000' in text
    assert not (ROOT / "build-evidence.json").exists()
    (source / source_path).write_text("changed")
    with pytest.raises(builder.BuildContractError, match="preimage digest mismatch"):
        builder.verify_preimages(source, patch["preimages"])


def test_header_factory_and_capability_require_exact_new_pair():
    selected = identity()
    assert patched.supports_amaru_header_validation(selected)
    assert TAP in patched.build_amaru_patched_factories(json_trace_paths=[], target_identity=selected)
    for changes in ({"patch_set_sha256": patched.AMARU_20260918_NANOSECOND_V3_PATCH_SHA256},
                    {"source_revision": patched.AMARU_SOURCE_REVISION}, {"mode": "stock"}):
        wrong = {**selected, **changes}
        assert not patched.supports_amaru_header_validation(wrong)
        assert TAP not in patched.build_amaru_patched_factories(json_trace_paths=[], target_identity=wrong)


def test_new_target_stays_unbuilt_without_registry_evidence(tmp_path):
    profile = SimpleNamespace(amaru_version="10.11.20260918", measurement_patch_revision=REVISION,
                              measurement_patch_set_sha256=manifest()["patch_set_sha256"])
    with pytest.raises(MeasurementTargetError, match="is not built"):
        resolve_patched_amaru_target(profile, registry_root=tmp_path)


def test_header_samples_keep_precise_timing_hash_outcomes_and_raw(tmp_path):
    result = collect(tmp_path, [event(), event("rejected", header_hash="b" * 64)])
    measures = result["measurements"]
    assert measures["header_validation"]["sample_count"] == 2
    assert measures["header_validation"]["mean"] == 1.234
    assert set(measures["header_validation_by_outcome"]) == {"accepted", "rejected"}
    assert result["exercise"]["required_outcomes_observed"] is True
    assert result["exercise"]["status"] == "pending-control-evidence"
    assert result["exercise"]["measurement_complete"] is False
    assert result["measurement_revision"] == "nanoseconds-v4"
    normalized = [json.loads(line) for line in (tmp_path / result["artifacts"]["normalized"]).read_text().splitlines()]
    assert [r["header_hash"] for r in normalized] == ["a" * 64, "b" * 64]
    assert normalized[0]["fields"]["elapsed_nanos"] == 1234
    assert (tmp_path / result["artifacts"]["raw_json"]).read_text().count("header_validation") == 2


@pytest.mark.parametrize("records", [[], [event()], [event("unavailable")]])
def test_empty_one_sided_and_prerequisite_failure_are_incomplete(tmp_path, records):
    result = collect(tmp_path, records)
    assert result["exercise"]["status"] == "incomplete"
    assert result["exercise"]["required_outcomes_observed"] is False
    assert result["exercise"]["measurement_complete"] is False
    expected = 1 if records and records[0]["fields"]["outcome"] == "accepted" else 0
    assert result["measurements"]["header_validation"]["sample_count"] == expected


@pytest.mark.parametrize("changes", [
    {"header_hash": ""}, {"header_hash": "not-a-hash"}, {"header_hash": int("1" * 64)},
    {"outcome": []}, {"error_kind": []}, {"outcome": "error"},
    {"outcome": "rejected", "error_kind": "nonces"}, {"elapsed_nanos": None},
    {"elapsed_nanos": True}, {"boundary": "other"}, {"elapsed_nanos": -1},
])
def test_invalid_header_records_are_rejected_not_samples(tmp_path, changes):
    result = collect(tmp_path, [event(**changes)])
    assert result["export"]["rejected_record_count"] == 1
    assert result["export"]["incomplete"] is True
    assert result["measurements"]["header_validation"]["sample_count"] == 0
    assert result["exercise"]["status"] == "incomplete"


def test_microsecond_only_and_untrusted_target_do_not_supply_header_evidence(tmp_path):
    record = event()
    del record["fields"]["elapsed_nanos"]
    other = event()
    other["target"] = "amaru::protocols"
    result = collect(tmp_path, [record, other])
    assert result["export"]["rejected_record_count"] == 2
    assert result["measurements"]["header_validation"]["sample_count"] == 0


def test_catalog_and_exercise_keep_runtime_proof_and_fallback_blocked():
    definition = yaml.safe_load(Path(f"dwarf/measurements/{TAP}.yaml").read_text())
    assert definition["compatibility"]["versions"] == [{"version": "10.11.20260918", "source_revision": REVISION}]
    assert definition["required_capabilities"] == ["amaru-patched-header-validation-instrumentation", "amaru-patch-identity"]
    suite = yaml.safe_load(Path("dwarf/qualification-suites/node-measurements-v1.yaml").read_text())
    exercise = next(e for e in suite["exercises"] if e["measurement_id"] == TAP)
    assert exercise["runtime"]["status"] == "implemented"
    assert "build" in exercise["runtime"]["reason"]
    assert exercise["fallback"]["status"] == "blocked"
    assert exercise["rule"]["outcomes"] == ["accepted", "rejected"]


def test_measurement_message_wins_over_enclosing_span_name(tmp_path):
    record = event()
    record["span"] = {"name": "validate_header_effect"}
    result = collect(tmp_path, [record])
    assert result["measurements"]["header_validation"]["sample_count"] == 1


@pytest.mark.parametrize("field", ["image_digest", "executable_digest", "build_result_sha256", "runtime_probe_log_sha256"])
def test_header_collector_requires_complete_immutable_build_provenance(tmp_path, field):
    selected = identity()
    del selected[field]
    collector = patched.AmaruPatchedCollector({"id": TAP}, json_trace_paths=[], target_identity=selected)
    # Supply a real trace path so missing provenance is the decisive failure.
    trace = tmp_path / "trace.ndjson"
    trace.write_text("")
    collector.json_paths = [trace]
    context = CollectorContext(measurement_id=TAP, definition={}, parameters={}, run_dir=tmp_path,
                               collector_dir=tmp_path / "measurements" / TAP)
    with pytest.raises(ValueError, match="immutable.*provenance|immutable image digest"):
        collector.prepare(context)
