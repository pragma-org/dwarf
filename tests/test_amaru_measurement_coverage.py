import hashlib
import json
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from profile_manager.measurement_targets import (
    MeasurementTargetError,
    coverage_target_record_path,
    resolve_coverage_amaru_target,
)
from scripts import aggregate_coverage
from scripts import build_amaru_coverage_target as builder
from scripts import cargo_fuzz_campaign


REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
HARNESS_ROOT = Path("dwarf/targets/amaru/coverage-targets") / REVISION


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity() -> dict:
    return {
        "schema_version": 1,
        "implementation": "amaru",
        "version": "10.11.20260912",
        "source_revision": REVISION,
        "mode": "coverage",
        "executable_digest": "sha256:" + "e" * 64,
        "build_result_sha256": "sha256:" + "b" * 64,
        "coverage_harness_sha256": "a" * 64,
        "target_name": "plutus_data",
        "engine": "cargo-fuzz/libFuzzer",
        "performance_authority": "non-authoritative",
        "non_authoritative_performance": True,
        "coverage_target_ref": "state:measurement-target-builds/amaru-coverage",
        "working_dir": "work/source",
        "fuzz_dir": "work/source/dwarf-coverage-fuzz",
        "binary": "work/source/dwarf-coverage-fuzz/target/release/plutus_data",
        "created_at": "2026-09-18T00:00:00Z",
    }


def test_checked_in_coverage_manifest_is_revision_locked_and_self_consistent():
    manifest = builder.load_manifest(HARNESS_ROOT / "manifest.json")

    assert manifest["source"] == {
        "repository": "https://github.com/pragma-org/amaru.git",
        "release": "10.11.20260912",
        "tag": "v10.11.20260912",
        "revision": REVISION,
    }
    assert manifest["toolchain"] == "nightly-2026-09-04"
    assert manifest["engine"] == "cargo-fuzz/libFuzzer"
    assert manifest["target_name"] == "plutus_data"
    assert manifest["production_entrypoint"] == (
        "amaru_kernel::from_cbor_no_leftovers::<amaru_kernel::PlutusData>"
    )
    assert manifest["non_authoritative_performance"] is True
    assert manifest["performance_authority"] == "non-authoritative"

    verified = builder.verify_harness_set(HARNESS_ROOT, manifest)
    assert verified["coverage_harness_sha256"] == manifest["coverage_harness_sha256"]
    assert {row["path"] for row in verified["files"]} == {
        "Cargo.toml",
        "fuzz_targets/plutus_data.rs",
    }
    assert all(
        row["sha256"] == _sha256(HARNESS_ROOT / row["path"])
        for row in verified["files"]
    )


def test_coverage_target_registry_fails_closed_and_returns_portable_identity(tmp_path):
    manifest = {
        "schema_version": 1,
        **_identity(),
        "coverage_target_ref": (
            "state:measurement-target-builds/amaru-b159172-coverage-plutus-data"
        ),
        "working_dir": "work/source",
        "fuzz_dir": "work/source/dwarf-coverage-fuzz",
        "binary": "work/source/dwarf-coverage-fuzz/target/release/plutus_data",
    }
    path = coverage_target_record_path(
        "amaru",
        REVISION,
        manifest["coverage_harness_sha256"],
        registry_root=tmp_path,
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    resolved = resolve_coverage_amaru_target(
        version="10.11.20260912",
        source_revision=REVISION,
        coverage_harness_sha256=manifest["coverage_harness_sha256"],
        registry_root=tmp_path,
    )

    assert resolved == manifest
    assert "/home/" not in json.dumps(resolved)
    assert "/Users/" not in json.dumps(resolved)
    assert resolved["non_authoritative_performance"] is True

    manifest["performance_authority"] = "authoritative"
    path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    with pytest.raises(MeasurementTargetError, match="non-authoritative"):
        resolve_coverage_amaru_target(
            version="10.11.20260912",
            source_revision=REVISION,
            coverage_harness_sha256=manifest["coverage_harness_sha256"],
            registry_root=tmp_path,
        )


def test_coverage_result_links_build_campaign_corpus_and_every_retained_input(tmp_path):
    bundle = tmp_path / "runs" / "run-coverage"
    summary = {
        "queue_count": 2,
        "crash_count": 0,
        "hang_count": 0,
        "queue_entries": [
            {"relative_path": "seed-a", "size_bytes": 2, "sha256": "1" * 64},
            {"relative_path": "id-000001", "size_bytes": 3, "sha256": "2" * 64},
        ],
        "coverage": {
            "covered_functions": 5,
            "total_functions": 10,
            "covered_lines": 20,
            "total_lines": 40,
            "covered_regions": 30,
            "total_regions": 60,
        },
    }

    result_path = cargo_fuzz_campaign.write_coverage_measurement_result(
        bundle_run_dir=bundle,
        coverage_identity=_identity(),
        campaign_id="amaru-plutus-data-0001",
        scenario_id="amaru-plutus-data-coverage-example",
        corpus_id="qualified-conway-plutus-data",
        corpus_digest="sha256:" + "c" * 64,
        target_name="plutus_data",
        summary=summary,
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert result_path == (
        bundle
        / "outputs"
        / "coverage"
        / "amaru-plutus-data-0001"
        / "result.json"
    )
    assert result["result_kind"] == "coverage"
    assert result["target"] == _identity()
    assert result["provenance"]["collector_mode"] == "compiler-coverage"
    assert result["provenance"]["dataset"] == {
        "corpus_id": "qualified-conway-plutus-data",
        "corpus_digest": "sha256:" + "c" * 64,
    }
    assert result["correlation"]["coverage_campaign_id"] == "amaru-plutus-data-0001"
    assert result["performance_authority"] == "non-authoritative"
    assert result["non_authoritative_performance"] is True
    assert [row["input_id"] for row in result["records"]] == [
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
    ]
    assert result["coverage"]["covered_lines"] == 20
    assert all(path.startswith("outputs/coverage/amaru-plutus-data-0001/") for path in result["artifacts"])
    schema = json.loads(
        Path("dwarf/spec/v1/measurement-result.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(result)


def test_coverage_result_refuses_a_machine_specific_target_identity(tmp_path):
    identity = _identity()
    identity["working_dir"] = "/home/nigel/source"

    with pytest.raises(ValueError, match="machine-specific|bounded relative"):
        cargo_fuzz_campaign.write_coverage_measurement_result(
            bundle_run_dir=tmp_path / "run",
            coverage_identity=identity,
            campaign_id="campaign",
            scenario_id="scenario",
            corpus_id="corpus",
            corpus_digest="sha256:" + "c" * 64,
            target_name="plutus_data",
            summary={},
        )


def test_existing_coverage_aggregation_preserves_authority_and_linkage(tmp_path):
    run_dir = tmp_path / "runs" / "run-coverage"
    cargo_dir = run_dir / "outputs" / "cargo-fuzz"
    cargo_dir.mkdir(parents=True)
    coverage_result = cargo_fuzz_campaign.write_coverage_measurement_result(
        bundle_run_dir=run_dir,
        coverage_identity=_identity(),
        campaign_id="amaru-plutus-data-0001",
        scenario_id="amaru-plutus-data-coverage-example",
        corpus_id="qualified-conway-plutus-data",
        corpus_digest="sha256:" + "c" * 64,
        target_name="plutus_data",
        summary={
            "queue_count": 1,
            "crash_count": 0,
            "hang_count": 0,
            "queue_entries": [
                {"relative_path": "seed", "size_bytes": 2, "sha256": "1" * 64}
            ],
            "coverage": {"covered_lines": 2, "total_lines": 4},
        },
    )
    (cargo_dir / "summary.json").write_text(
        json.dumps(
            {
                "queue_count": 1,
                "crash_count": 0,
                "hang_count": 0,
                "queue_entries": [{"sha256": "1" * 64}],
                "coverage_result": str(coverage_result.relative_to(run_dir)),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    entry = aggregate_coverage._load_cargo_fuzz_bundle(
        run_dir, runs_root=tmp_path / "runs"
    )

    assert entry["target"] == "amaru"
    assert entry["target_identity"] == _identity()
    assert entry["coverage_campaign_id"] == "amaru-plutus-data-0001"
    assert entry["corpus_id"] == "qualified-conway-plutus-data"
    assert entry["performance_authority"] == "non-authoritative"
    assert entry["non_authoritative_performance"] is True


def test_campaign_export_preserves_the_actual_fuzz_target_name(tmp_path, monkeypatch):
    output_dir = tmp_path / "campaign"
    queue = output_dir / "default" / "queue"
    queue.mkdir(parents=True)
    (queue / "seed").write_bytes(b"\x01")
    bundle = tmp_path / "runs" / "run-coverage"

    monkeypatch.setattr(
        cargo_fuzz_campaign.testcase_lifecycle,
        "build_testcase_records",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        cargo_fuzz_campaign.testcase_lifecycle,
        "write_lifecycle_artifacts",
        lambda **_kwargs: {},
    )

    summary_path = cargo_fuzz_campaign.export_campaign_artifacts_with_metadata(
        output_dir=output_dir,
        bundle_run_dir=bundle,
        target_name="plutus_data",
        target_implementation="amaru",
        replay_harness="amaru-cbor-decode-plutus-data",
        replay_target_id="different-replay-id",
        replay_targets=["library"],
        coverage_identity=_identity(),
        coverage_campaign_id="amaru-plutus-data-0001",
        scenario_id="amaru-plutus-data-coverage-example",
        corpus_id="qualified-conway-plutus-data",
        corpus_digest="sha256:" + "c" * 64,
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    result = json.loads(
        (bundle / summary["coverage_result"]).read_text(encoding="utf-8")
    )
    assert result["campaign"]["target_name"] == "plutus_data"
    assert result["correlation"]["target_name"] == "plutus_data"


def test_compiler_coverage_summary_contains_only_portable_artifact_paths(tmp_path):
    coverage_dir = tmp_path / "campaign" / "coverage"
    raw_dir = coverage_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "one.profraw").write_bytes(b"profile")
    binary = tmp_path / "build" / "plutus_data"
    binary.parent.mkdir()
    binary.write_bytes(b"binary")
    profdata = tmp_path / "llvm-profdata"
    profdata.write_text(
        "#!/bin/sh\nfor arg in \"$@\"; do prev=$out; out=$arg; "
        "if [ \"$prev\" = '-o' ]; then : > \"$arg\"; fi; done\n",
        encoding="utf-8",
    )
    llvm_cov = tmp_path / "llvm-cov"
    llvm_cov.write_text(
        "#!/bin/sh\nprintf '%s\\n' "
        "'{\"data\":[{\"totals\":{\"functions\":{\"covered\":2,\"count\":4},"
        "\"lines\":{\"covered\":3,\"count\":6},\"regions\":{\"covered\":5,\"count\":10}}}]}'\n",
        encoding="utf-8",
    )
    for tool in (profdata, llvm_cov):
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)

    summary = cargo_fuzz_campaign.merge_coverage_profiles(
        coverage_dir=coverage_dir,
        target_binary=binary,
        llvm_profdata=profdata,
        llvm_cov=llvm_cov,
    )

    assert summary["target_binary"] == "plutus_data"
    assert summary["profraw_files"] == ["raw/one.profraw"]
    assert summary["profdata_path"] == "default.profdata"
    assert str(tmp_path) not in json.dumps(summary)
