import hashlib
import json
from pathlib import Path

import pytest

from profile_manager.measurement_targets import (
    MeasurementTargetError,
    coverage_target_record_path,
    resolve_coverage_cardano_target,
)
from scripts import build_cardano_coverage_target as builder


REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
HARNESS_ROOT = Path("dwarf/targets/cardano-node/coverage-targets") / REVISION


def test_manifest_is_exact_revision_locked_and_non_authoritative():
    manifest = builder.load_manifest(HARNESS_ROOT / "manifest.json")

    assert manifest["source"] == {
        "repository": "https://github.com/IntersectMBO/cardano-node.git",
        "release": "11.1.2",
        "tag": "11.1.2",
        "revision": REVISION,
    }
    assert manifest["toolchain"] == {"ghc": "9.6.7", "cabal": "3.16.0.0"}
    assert manifest["engine"] == "GHC HPC corpus replay"
    assert manifest["performance_authority"] == "non-authoritative"
    assert manifest["non_authoritative_performance"] is True
    assert manifest["target_names"] == [
        "cardano-node-production-handshake-coverage"
    ]
    assert manifest["build"]["instrumented_packages"] == ["cardano-diffusion"]
    assert manifest["build"]["ghc_options"] == ["-fhpc"]
    assert manifest["dependencies"] == [{
        "package": "cardano-diffusion",
        "version": "1.1.1.0",
        "archive": "cardano-diffusion-1.1.1.0.tar.gz",
        "sha256": "50c0fceec6bcc8b37f9eefaf4fcb0544eae5108c2413d5d87a9833b623fdc549",
    }]

    verified = builder.verify_harness_set(
        HARNESS_ROOT, manifest
    )
    assert verified["coverage_harness_sha256"] == manifest["coverage_harness_sha256"]
    assert verified["files"]


def test_registry_identity_is_portable_and_fails_closed(tmp_path):
    result = {
        "schema_version": 1,
        "created_at": "2026-09-19T00:00:00Z",
        "source": {
            "release": "11.1.2",
            "revision": REVISION,
        },
        "engine": "GHC HPC corpus replay",
        "coverage_harness_sha256": "a" * 64,
        "target_names": ["cardano-node-mini-protocol-decode-handshake"],
        "executables": [
            {
                "target_name": "cardano-node-mini-protocol-decode-handshake",
                "path": "work/source/dist-newstyle/handshake",
                "sha256": "e" * 64,
                "size_bytes": 123,
            }
        ],
        "build_result_sha256": "b" * 64,
        "coverage_target_ref": "state:measurement-target-builds/cardano-coverage",
    }
    record = builder.publish_target_record(result, registry_root=tmp_path)

    assert record["implementation"] == "cardano-node"
    assert record["mode"] == "coverage"
    assert record["source_revision"] == REVISION
    assert record["performance_authority"] == "non-authoritative"
    assert record["non_authoritative_performance"] is True
    serialized = json.dumps(record, sort_keys=True)
    assert "/home/" not in serialized
    assert "/Users/" not in serialized

    resolved = resolve_coverage_cardano_target(
        version="11.1.2",
        source_revision=REVISION,
        coverage_harness_sha256="a" * 64,
        registry_root=tmp_path,
    )
    assert resolved == record

    path = coverage_target_record_path(
        "cardano-node", REVISION, "a" * 64, registry_root=tmp_path
    )
    record["performance_authority"] = "authoritative"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(MeasurementTargetError, match="non-authoritative"):
        resolve_coverage_cardano_target(
            version="11.1.2",
            source_revision=REVISION,
            coverage_harness_sha256="a" * 64,
            registry_root=tmp_path,
        )


def test_builder_refuses_wrong_or_dirty_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    builder.run_checked(["git", "init", "-q"], cwd=source)
    builder.run_checked(["git", "config", "user.email", "fixture@example.invalid"], cwd=source)
    builder.run_checked(["git", "config", "user.name", "fixture"], cwd=source)
    (source / "tracked").write_text("exact\n", encoding="utf-8")
    builder.run_checked(["git", "add", "tracked"], cwd=source)
    builder.run_checked(["git", "commit", "-q", "-m", "fixture"], cwd=source)
    revision = builder.git(source, "rev-parse", "HEAD")

    assert builder.validate_clean_source(source, revision) == revision
    with pytest.raises(builder.BuildContractError, match="revision"):
        builder.validate_clean_source(source, "0" * 40)
    (source / "tracked").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(builder.BuildContractError, match="not clean"):
        builder.validate_clean_source(source, revision)


def test_harness_digest_detects_any_changed_source(tmp_path):
    manifest = builder.load_manifest(HARNESS_ROOT / "manifest.json")
    copied = tmp_path / "harness"
    builder.copy_harness_files(
        HARNESS_ROOT, copied, manifest
    )
    target = copied / manifest["harness_files"][0]["path"]
    target.write_bytes(target.read_bytes() + b"changed")

    with pytest.raises(builder.BuildContractError, match="digest mismatch"):
        builder.verify_harness_set(copied, manifest)


def test_result_digest_is_stable_over_canonical_json():
    body = {"z": 1, "a": [2, 3]}
    expected = hashlib.sha256(
        (json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    assert builder.canonical_digest(body) == expected


def test_dependency_archive_digest_fails_closed(tmp_path):
    archive = tmp_path / "dependency.tar.gz"
    archive.write_bytes(b"exact dependency")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert builder.verify_dependency_archive(archive, digest) == digest
    with pytest.raises(builder.BuildContractError, match="dependency archive digest"):
        builder.verify_dependency_archive(archive, "0" * 64)
