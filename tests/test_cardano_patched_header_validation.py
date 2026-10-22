"""Synthetic contract checks; no build, node execution or runtime proof."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from profile_manager.measurement_collectors import cardano_patched as patched
from profile_manager.measurement_runtime import CollectorContext
from profile_manager.measurement_targets import MeasurementTargetError, resolve_patched_cardano_target
from scripts import build_cardano_measurement_target as builder

REVISION = patched.CARDANO_SOURCE_REVISION
TAP = "cardano-patched-header-validation"
ROOT = Path("dwarf/targets/cardano-node/measurement-patches-nanoseconds-v3") / REVISION
HEADER_PATH = "ouroboros-consensus/src/ouroboros-consensus/Ouroboros/Consensus/HeaderValidation.hs"
NETWORK_PATH = "api/lib/Ouroboros/Network/DwarfMeasurement.hs"


def manifest():
    return json.loads((ROOT / "manifest.json").read_text())


def identity(**changes):
    return {"implementation": "cardano-node", "version": "11.1.2", "mode": "patched",
            "source_revision": REVISION, "patch_set_sha256": manifest()["patch_set_sha256"],
            "toolchain": {"ghc": "9.6.7", "cabal": "3.16.0.0"},
            "image_digest": "sha256:" + "1" * 64, "executable_digest": "sha256:" + "2" * 64,
            "build_result_sha256": "sha256:" + "3" * 64,
            "runtime_probe_log_sha256": "sha256:" + "4" * 64, **changes}


def event(outcome="accepted", **changes):
    return {"schema_version": "v1", "target": "cardano-node::measurement",
            "event": "header_validation", "boundary": "consensus-validate-header",
            "header_hash": "a" * 64, "outcome": outcome,
            "error_kind": {"accepted": "none", "rejected": "header-protocol", "unavailable": "evaluation-exception"}.get(outcome if isinstance(outcome, str) else "invalid", "none"),
            "reason": "" if outcome != "rejected" else "HeaderProtocolError InvalidVRFProof",
            "ended_monotonic_ns": 3000, "elapsed_nanos": 1234, "duration_us": 1, **changes}


def collect(tmp_path, records, selected=None):
    trace = tmp_path / "trace.ndjson"
    trace.write_text("".join(json.dumps(row) + "\n" for row in records))
    context = CollectorContext(run_dir=tmp_path, collector_dir=tmp_path / "measurements" / TAP,
                               measurement_id=TAP, definition={}, parameters={})
    collector = patched.CardanoPatchedCollector({"id": TAP}, json_trace_paths=[trace],
                                               target_identity=selected or identity(), include_existing=True)
    collector.prepare(context)
    collector.start(context)
    return collector.finalize(context)


def test_exact_manifest_and_retained_preimages_apply_without_build(tmp_path):
    m = manifest()
    assert m["source"]["revision"] == REVISION
    assert m["toolchain"] == {"ghc": "9.6.7", "cabal": "3.16.0.0"}
    assert m["measurement_revision"] == "nanoseconds-v3"
    assert builder.verify_patch_set(ROOT, m)["patch_set_sha256"] == m["patch_set_sha256"]
    old = json.loads((Path(str(ROOT).replace("nanoseconds-v3", "nanoseconds-v2")) / "manifest.json").read_text())
    assert m["patches"][:3] == old["patches"]
    for patch, fixture, source_path in zip(m["patches"][3:],
            ["cardano-network-v2-DwarfMeasurement.hs", "cardano-consensus-4.2.1.0-HeaderValidation.hs"],
            [NETWORK_PATH, HEADER_PATH]):
        data = (Path("tests/fixtures") / fixture).read_bytes()
        assert patch["preimages"] == [{"path": source_path, "sha256": hashlib.sha256(data).hexdigest()}]
        root = tmp_path / str(len(data))
        (root / source_path).parent.mkdir(parents=True)
        (root / source_path).write_bytes(data)
        builder.verify_preimages(root, patch["preimages"])
        subprocess.run(["git", "apply", "--check", "--whitespace=error-all", str((ROOT / patch["path"]).resolve())], cwd=root, check=True)
        subprocess.run(["git", "apply", "--whitespace=error-all", str((ROOT / patch["path"]).resolve())], cwd=root, check=True)
        text = (root / source_path).read_text()
        if source_path == NETWORK_PATH:
            helper = text.split("measureDwarfHeader headerHash", 1)[1]
            assert "try (evaluate value)" in helper
            assert "Right (Right _)" in helper
            assert "throwIO (failure :: SomeException)" in helper
            assert "evaluate payload" not in helper
            assert "take 1024" in helper
            assert "tryHeaderInstrumentation :: IO a -> IO (Either SomeException a)" in helper
            assert '"duration_us" .= (elapsedNs `div` 1000)' in helper
        else:
            assert 'measureDwarfHeader (show (headerHash hdr))' in text
            assert 'classifyError HeaderProtocolError{} = "header-protocol"' in text
            assert 'classifyError HeaderEnvelopeError{} = "header-envelope"' in text
            assert "validateHeaderInner cfg ledgerView hdr st = do" in text
    assert not (ROOT / "build-evidence.json").exists()


def test_capability_and_factory_only_for_exact_new_identity():
    assert patched.supports_cardano_header_validation(identity())
    assert TAP in patched.build_cardano_patched_factories(json_trace_paths=[], target_identity=identity())
    for changes in ({"patch_set_sha256": "09b7d2b1bac9808128f88645f96e25c5ace095213bf299717f5287d11dfc7375"},
                    {"patch_set_sha256": "81b5f3e2cbff8d27bb40946097ad58c4b0dee38ce634922854ead4d04b7c158d"},
                    {"patch_set_sha256": patched.CARDANO_NANOSECOND_PATCH_SHA256},
                    {"patch_set_sha256": patched.CARDANO_MEASUREMENT_PATCH_SHA256},
                    {"source_revision": "a" * 40}, {"mode": "stock"}, {"toolchain": {}},
                    {"toolchain": {"ghc": "9.8.4", "cabal": "3.16.0.0"}}):
        selected = identity(**changes)
        assert not patched.supports_cardano_header_validation(selected)
        assert TAP not in patched.build_cardano_patched_factories(json_trace_paths=[], target_identity=selected)


def test_target_stays_unbuilt_without_registry_evidence(tmp_path):
    profile = SimpleNamespace(cardano_version="11.1.2", measurement_patch_revision=REVISION,
                              measurement_patch_set_sha256=manifest()["patch_set_sha256"])
    with pytest.raises(MeasurementTargetError, match="is not built"):
        resolve_patched_cardano_target(profile, registry_root=tmp_path)


def test_header_samples_timing_native_outcomes_correlation_and_controls(tmp_path):
    result = collect(tmp_path, [event(), event("rejected", header_hash="b" * 64), event("unavailable")])
    assert result["measurement_revision"] == "nanoseconds-v3"
    assert result["measurements"]["header_validation"]["sample_count"] == 2
    assert result["measurements"]["header_validation"]["mean"] == 1.234
    assert result["measurements"]["header_prerequisite_unavailable"]["sample_count"] == 1
    assert result["exercise"]["required_outcomes_observed"] is True
    assert result["exercise"]["measurement_complete"] is False
    assert result["exercise"]["status"] == "incomplete"
    assert result["exercise"]["unavailable_event_count"] == 1
    correlations = json.loads((tmp_path / result["artifacts"]["correlations"]).read_text())
    assert correlations["indexes"]["headers"]["b" * 64] == [1]
    raw = (tmp_path / result["artifacts"]["raw_json"]).read_text()
    assert "HeaderProtocolError InvalidVRFProof" in raw


@pytest.mark.parametrize("records,status", [([], "incomplete"), ([event()], "incomplete"),
    ([event(), event("rejected")], "pending-control-evidence"), ([event("unavailable")], "incomplete")])
def test_no_runtime_completeness_inferred_from_trace(tmp_path, records, status):
    result = collect(tmp_path, records)
    assert result["exercise"]["status"] == status
    assert result["exercise"]["measurement_complete"] is False


@pytest.mark.parametrize("changes", [
    {"schema_version": "v2"}, {"boundary": "other"}, {"target": "untrusted"},
    {"header_hash": "bad"}, {"header_hash": 123}, {"outcome": []}, {"error_kind": []},
    {"outcome": "rejected", "error_kind": "evaluation-exception"},
    {"outcome": "accepted", "reason": "oops"}, {"reason": "a" * 1025}, {"reason": "bad\nline"},
    {"elapsed_nanos": None}, {"elapsed_nanos": True}, {"elapsed_nanos": -1},
    {"ended_monotonic_ns": 1}, {"duration_us": 2}, {"duration_us": True},
])
def test_invalid_records_are_rejected_not_samples(tmp_path, changes):
    result = collect(tmp_path, [event(**changes)])
    assert result["export"]["rejected_record_count"] == 1
    assert result["measurements"]["header_validation"]["sample_count"] == 0
    assert result["exercise"]["status"] == "incomplete"


@pytest.mark.parametrize("field", ["image_digest", "executable_digest", "build_result_sha256", "runtime_probe_log_sha256"])
def test_header_collector_requires_immutable_build_provenance(tmp_path, field):
    selected = identity()
    del selected[field]
    with pytest.raises(ValueError, match="immutable"):
        collect(tmp_path, [event()], selected=selected)


def test_catalog_marks_contract_implemented_with_build_and_runtime_blockers():
    definition = yaml.safe_load(Path(f"dwarf/measurements/{TAP}.yaml").read_text())
    assert definition["required_capabilities"] == ["cardano-patched-header-validation-instrumentation", "cardano-patch-identity"]
    suite = yaml.safe_load(Path("dwarf/qualification-suites/node-measurements-v1.yaml").read_text())
    exercise = next(row for row in suite["exercises"] if row["measurement_id"] == TAP)
    assert exercise["runtime"]["status"] == "implemented"
    assert "build" in exercise["runtime"]["reason"]
    assert exercise["fallback"]["status"] == "blocked"


def test_missing_precise_timing_and_legacy_identity_cannot_supply_header_evidence(tmp_path):
    record = event()
    del record["elapsed_nanos"]
    result = collect(tmp_path, [record])
    assert result["export"]["rejected_record_count"] == 1
    with pytest.raises(ValueError, match="exact source, patch and toolchain"):
        collect(tmp_path, [event()], selected=identity(patch_set_sha256=patched.CARDANO_NANOSECOND_PATCH_SHA256))




def test_envelope_error_is_a_native_rejection_and_preimage_tampering_fails(tmp_path):
    result = collect(tmp_path, [event("rejected", error_kind="header-envelope", reason="HeaderEnvelopeError UnexpectedBlockNo")])
    assert result["measurements"]["header_validation_by_outcome"]["rejected"]["sample_count"] == 1
    patch = manifest()["patches"][-1]
    path = tmp_path / HEADER_PATH
    path.parent.mkdir(parents=True)
    path.write_text("changed")
    with pytest.raises(builder.BuildContractError, match="preimage digest mismatch"):
        builder.verify_preimages(tmp_path, patch["preimages"])


def test_retained_network_preimage_is_exact_output_of_inherited_patch():
    first = (ROOT / manifest()["patches"][0]["path"]).read_text()
    section = first.split("diff --git a/" + NETWORK_PATH, 1)[1].split("diff --git ", 1)[0]
    expected = "".join(line[1:] + "\n" for line in section.splitlines() if line.startswith("+") and not line.startswith("+++"))
    assert Path("tests/fixtures/cardano-network-v2-DwarfMeasurement.hs").read_text() == expected


def test_diagnostic_failure_fallback_is_unavailable_without_fabricated_hash_or_timing(tmp_path):
    fallback = {"schema_version": "v1", "target": "cardano-node::measurement",
                "event": "header_validation", "boundary": "consensus-validate-header",
                "outcome": "unavailable", "error_kind": "instrumentation-exception"}
    result = collect(tmp_path, [event(), event("rejected"), fallback])
    assert result["export"]["rejected_record_count"] == 0
    assert result["measurements"]["header_validation"]["sample_count"] == 2
    assert result["measurements"]["header_prerequisite_unavailable"]["sample_count"] == 0
    assert result["exercise"]["unavailable_event_count"] == 1
    assert result["exercise"]["status"] == "incomplete"
    normalized = [json.loads(line) for line in (tmp_path / result["artifacts"]["normalized"]).read_text().splitlines()]
    assert "header_hash" not in normalized[-1]
    assert "elapsed_nanos" not in normalized[-1]
    assert "duration_us" not in normalized[-1]


def test_diagnostic_guard_covers_encoding_and_fallback_before_native_rethrow(tmp_path):
    source = tmp_path / NETWORK_PATH
    source.parent.mkdir(parents=True)
    source.write_bytes(Path("tests/fixtures/cardano-network-v2-DwarfMeasurement.hs").read_bytes())
    subprocess.run(["git", "apply", str((ROOT / manifest()["patches"][3]["path"]).resolve())], cwd=tmp_path, check=True)
    helper = source.read_text().split("measureDwarfHeader headerHash", 1)[1]
    guard = helper.index("emitted <- tryHeaderInstrumentation $ do")
    preserve = helper.index("case evaluated of\n        Left failure -> throwIO")
    for expression in ("classifyError err", "renderError err", "take 65 headerHash", "take 1024 reason", "LBS.toStrict $ encode", "BS.hPutStrLn handle encoded"):
        assert guard < helper.index(expression) < preserve
    assert "sink <- tryHeaderInstrumentation (evaluate measurementHandle)" in helper
    assert helper.index("started <- tryHeaderInstrumentation getMonotonicTimeNSec") < helper.index("evaluated <- try (evaluate value)") < guard
    assert guard < helper.index("endedNs <- getMonotonicTimeNSec") < preserve
    assert helper.index("emitHeaderUnavailable lock") < preserve
    assert "tryHeaderInstrumentation :: IO a -> IO (Either SomeException a)" in helper
    assert "Right (Right _)" in helper
    assert "evaluate payload" not in helper
    fallback = helper.split("emitHeaderUnavailable lock =", 1)[1]
    assert "tryHeaderInstrumentation" in fallback
    for forbidden in ("headerHash", "classifyError", "renderError", "encode", "evaluated", "getMonotonicTimeNSec"):
        assert forbidden not in fallback
    assert 'instrumentation-exception' in fallback
    literal = re.search(r'BS.hPutStrLn handle\n\s+("(?:\\.|[^"\\])*")', fallback)
    assert literal is not None
    record = json.loads(json.loads(literal.group(1)))
    assert record == {"schema_version": "v1", "target": "cardano-node::measurement",
                      "event": "header_validation", "boundary": "consensus-validate-header",
                      "outcome": "unavailable", "error_kind": "instrumentation-exception"}


@pytest.mark.parametrize("extra", [{"header_hash": "a" * 64}, {"elapsed_nanos": 0}, {"duration_us": 0}, {"reason": "diagnostic"}])
def test_minimal_instrumentation_fallback_cannot_claim_correlation_or_timing(tmp_path, extra):
    fallback = {"schema_version": "v1", "target": "cardano-node::measurement",
                "event": "header_validation", "boundary": "consensus-validate-header",
                "outcome": "unavailable", "error_kind": "instrumentation-exception", **extra}
    result = collect(tmp_path, [fallback])
    assert result["export"]["rejected_record_count"] == 1
    assert result["measurements"]["header_validation"]["sample_count"] == 0
    assert result["exercise"]["measurement_complete"] is False


def test_thread_killed_and_user_interrupt_are_rethrown_before_telemetry(tmp_path):
    source = tmp_path / NETWORK_PATH
    source.parent.mkdir(parents=True)
    source.write_bytes(Path("tests/fixtures/cardano-network-v2-DwarfMeasurement.hs").read_bytes())
    subprocess.run(["git", "apply", str((ROOT / manifest()["patches"][3]["path"]).resolve())], cwd=tmp_path, check=True)
    text = source.read_text()
    assert "SomeAsyncException" in text.split("import Control.Exception", 1)[1].split("\n", 1)[0]
    helper = text.split("measureDwarfHeader headerHash", 1)[1]
    native = helper.split("evaluated <- try (evaluate value)", 1)[1].split("emitted <- tryHeaderInstrumentation", 1)[0]
    assert "Left failure -> rethrowHeaderAsync failure" in native
    assert "Right _ -> pure ()" in native
    for forbidden in ("getMonotonicTimeNSec", "headerHash", "classifyError", "renderError", "encode", "withMVar", "emitHeaderUnavailable"):
        assert forbidden not in native
    guard = helper.split("tryHeaderInstrumentation action =", 1)[1].split("rethrowHeaderAsync ::", 1)[0]
    assert "Left failure -> rethrowHeaderAsync failure >> pure result" in guard
    assert "Right _ -> pure result" in guard
    classifier = helper.split("rethrowHeaderAsync failure =", 1)[1].split("emitHeaderUnavailable ::", 1)[0]
    assert "fromException failure :: Maybe SomeAsyncException" in classifier
    assert "Just _ -> throwIO failure" in classifier
    assert "Nothing -> pure ()" in classifier
    assert "ThreadKilled" in classifier and "UserInterrupt" in classifier
    assert "emitHeaderUnavailable" not in classifier
