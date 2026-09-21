import json
from pathlib import Path

import pytest

from scripts import build_cardano_conformance_adapter as builder


REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
ADAPTER_ROOT = Path("dwarf/targets/cardano-node/conformance-adapters") / REVISION


def test_manifest_is_exact_revision_locked_and_digest_verified():
    manifest = builder.load_manifest(ADAPTER_ROOT / "manifest.json")

    assert manifest["kind"] == "production-cbor-conformance"
    assert manifest["source"] == {
        "repository": "https://github.com/IntersectMBO/cardano-node.git",
        "release": "11.1.2",
        "tag": "11.1.2",
        "revision": REVISION,
    }
    assert manifest["measurement_boundary"] == "production-codec-only"
    assert manifest["toolchain"] == {"ghc": "9.6.7", "cabal": "3.16.0.0"}
    verified = builder.verify_adapter_set(ADAPTER_ROOT, manifest)
    assert verified["adapter_set_sha256"] == manifest["adapter_set_sha256"]
    assert [row["path"] for row in verified["files"]] == [
        "cbor/cabal.project",
        "cbor/dwarf-cardano-cbor-conformance.cabal",
        "cbor/src/Main.hs",
    ]


def test_adapter_digest_detects_changed_source(tmp_path):
    manifest = builder.load_manifest(ADAPTER_ROOT / "manifest.json")
    copied = tmp_path / "adapter"
    builder.copy_adapter_files(ADAPTER_ROOT, copied, manifest)
    target = copied / manifest["adapter_files"][0]["path"]
    target.write_bytes(target.read_bytes() + b"changed")

    with pytest.raises(builder.BuildContractError, match="digest mismatch"):
        builder.verify_adapter_set(copied, manifest)


def test_registry_record_is_portable_and_revision_locked(tmp_path):
    result = {
        "schema_version": 1,
        "created_at": "2026-09-20T00:00:00Z",
        "kind": "production-cbor-conformance",
        "implementation": "cardano-node",
        "source_revision": REVISION,
        "source_release": "11.1.2",
        "measurement_boundary": "production-codec-only",
        "production_entrypoint": "decodeFullAnnotator",
        "adapter_set_sha256": "a" * 64,
        "adapter_files": [],
        "build_command": ["$HOME/.ghcup/bin/cabal-3.16.0.0", "build", "-w", "$HOME/.ghcup/bin/ghc-9.6.7"],
        "build_log_sha256": "b" * 64,
        "executable": "/disposable/source/dist-newstyle/adapter",
        "executable_sha256": "c" * 64,
        "build_result_sha256": "d" * 64,
        "toolchain": {"ghc": "9.6.7", "cabal": "3.16.0.0"},
    }

    record = builder.publish_adapter_record(result, registry_root=tmp_path)
    assert record["source_revision"] == REVISION
    assert record["executable_ref"].startswith("build:")
    assert "executable" not in record
    serialized = json.dumps(record, sort_keys=True)
    assert "/home/" not in serialized
    assert "/disposable/" not in serialized


def test_runtime_record_retains_resolvable_executable_and_build_digest(tmp_path):
    result = {
        "source_revision": REVISION,
        "executable": "/disposable/source/dist-newstyle/adapter",
        "executable_sha256": "c" * 64,
        "build_result_sha256": "d" * 64,
    }
    path = builder.write_runtime_record(result, output_dir=tmp_path)
    body = json.loads(path.read_text())
    assert body == result
    assert path == tmp_path / "evidence" / "runtime-record.json"


def test_cardano_input_is_fully_read_before_codec_timer_starts():
    source = (ADAPTER_ROOT / "cbor/src/Main.hs").read_text()
    forced_input = source.index("evaluate (BSL.length bytes)")
    timer_start = source.index("startedNs <- getMonotonicTimeNSec")
    decode = source.index("let decoded = decodeFullAnnotator")
    assert forced_input < timer_start < decode


def test_cardano_rejected_timer_stops_before_error_presentation():
    source = (ADAPTER_ROOT / "cbor/src/Main.hs").read_text()
    rejected = source[source.index("Left err -> do") : source.index("Right (value :: Data ConwayEra)")]
    assert "evaluate (force (show err))" not in rejected
    assert rejected.index("endedNs <- getMonotonicTimeNSec") < rejected.index('"error" .= show err')
