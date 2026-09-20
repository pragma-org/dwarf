from pathlib import Path

from scripts import build_amaru_conformance_adapter as builder


REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
ADAPTER_ROOT = Path("dwarf/targets/amaru/conformance-adapters") / REVISION


def test_manifest_is_exact_revision_locked_and_digest_verified():
    manifest = builder.exact_builder.load_manifest(ADAPTER_ROOT / "manifest.json")
    files, digest = builder._verify_files(ADAPTER_ROOT / "manifest.json", manifest)

    assert manifest["kind"] == "production-cbor-conformance"
    assert manifest["source"]["revision"] == REVISION
    assert manifest["measurement_boundary"] == "production-codec-only"
    assert digest == manifest["adapter_set_sha256"]
    assert [row["path"] for row in files] == ["cbor/Cargo.toml", "cbor/src/main.rs"]
