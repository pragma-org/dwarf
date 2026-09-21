from pathlib import Path

import pytest

from scripts import build_amaru_conformance_adapter as builder


REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
ADAPTER_ROOT = Path("dwarf/targets/amaru/conformance-adapters") / REVISION
FIXED_REVISION = "d3a6dafcced78f5809a96619e883cf04911d2bdc"
FIXED_ADAPTER_ROOT = Path("dwarf/targets/amaru/conformance-adapters") / FIXED_REVISION


def test_manifest_is_exact_revision_locked_and_digest_verified():
    manifest = builder.exact_builder.load_manifest(ADAPTER_ROOT / "manifest.json")
    files, digest = builder._verify_files(ADAPTER_ROOT / "manifest.json", manifest)

    assert manifest["kind"] == "production-cbor-conformance"
    assert manifest["source"]["revision"] == REVISION
    assert manifest["measurement_boundary"] == "production-codec-only"
    assert digest == manifest["adapter_set_sha256"]
    assert [row["path"] for row in files] == ["cbor/Cargo.toml", "cbor/src/main.rs"]


def test_fixed_revision_manifest_selects_its_own_verified_source():
    manifest_path = FIXED_ADAPTER_ROOT / "manifest.json"
    manifest = builder.exact_builder.load_manifest(manifest_path)
    files, digest = builder._verify_files(manifest_path, manifest)

    assert builder._manifest_revision(manifest_path, manifest) == FIXED_REVISION
    assert manifest["source"]["revision"] == FIXED_REVISION
    assert manifest["upstream_fix"] == FIXED_REVISION
    assert digest == manifest["adapter_set_sha256"]
    assert [row["path"] for row in files] == ["cbor/Cargo.toml", "cbor/src/main.rs"]
    source = (FIXED_ADAPTER_ROOT / "cbor/src/main.rs").read_text()
    assert f'const SOURCE_REVISION: &str = "{FIXED_REVISION}";' in source
    assert "from_cbor_no_leftovers::<PlutusData>" in source


def test_adapter_manifest_revision_must_match_its_revision_directory(tmp_path):
    manifest_path = tmp_path / ("a" * 40) / "manifest.json"
    manifest_path.parent.mkdir()
    manifest = {"source": {"revision": FIXED_REVISION}}

    with pytest.raises(builder.BuildContractError, match="revision directory"):
        builder._manifest_revision(manifest_path, manifest)


def test_fixed_revision_build_evidence_retains_passing_corpus_and_digests():
    evidence = builder.exact_builder.load_manifest(
        FIXED_ADAPTER_ROOT / "build-evidence.json"
    )

    assert evidence["source_revision"] == FIXED_REVISION
    assert evidence["manifest_sha256"] == "8877a4ae7f46521ae1a8bf6584e4092b39ef3f0f8acf93bbcc4e0bd551f1f685"
    assert evidence["adapter_set_sha256"] == "1fe5ef1eba105e88c781a9692bc44887f94431f580d1150c328b33cd91bb1561"
    assert evidence["executable_sha256"] == "dcc7936b562ef93b10a38aa366adaeeb7dfea05e826030075100316d07e3370d"
    assert evidence["corpus"]["input_count"] == 100
    assert evidence["corpus"]["outcome_mismatch_count"] == 0
    assert evidence["corpus"]["roundtrip_failure_count"] == 0
    assert evidence["corpus"]["selection_sha256"] == "3ed74e02f2f2be02ad942d26c193a125a34a0828373aac11cbf5efe082fdd467"
