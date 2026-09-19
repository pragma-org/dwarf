import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import build_amaru_measurement_target as builder


REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
PATCH_ROOT = (
    Path("dwarf/targets/amaru/measurement-patches") / REVISION
)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--quiet")
    _git(source, "config", "user.name", "DWARF test")
    _git(source, "config", "user.email", "dwarf-test@example.invalid")
    (source / "tracked.txt").write_text("original\n")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "--quiet", "-m", "fixture")
    return source, _git(source, "rev-parse", "HEAD")


def test_checked_in_manifest_is_exact_bounded_and_self_consistent():
    manifest = builder.load_manifest(PATCH_ROOT / "manifest.json")

    assert manifest["source"]["revision"] == REVISION
    assert manifest["source"]["release"] == "10.11.20260912"
    assert manifest["toolchain"] == "nightly-2026-09-04"
    assert manifest["build"]["command"] == [
        "cargo",
        "+nightly-2026-09-04",
        "zigbuild",
        "--locked",
        "--release",
        "-p",
        "amaru",
        "--bin",
        "amaru",
        "--target",
        "x86_64-unknown-linux-musl",
    ]
    assert manifest["build"]["binary"] == (
        "target/x86_64-unknown-linux-musl/release/amaru"
    )
    assert manifest["build_tools"] == {
        "cargo-zigbuild": "0.23.4",
        "zig": "0.15.2",
        "target": "x86_64-unknown-linux-musl",
    }
    assert manifest["runtime_probe_image"].endswith(
        "@sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36"
    )
    assert manifest["base_image"].endswith(
        "@sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e"
    )
    assert {item["capability"] for item in manifest["instrumentation"]} == {
        "live-protocol-cbor-decode",
        "blockfetch-handler-queue",
        "txsubmission2-residence",
    }
    assert all(item["all_outcomes_timed"] for item in manifest["instrumentation"])
    assert all(
        event.startswith("amaru::protocols/measurement.")
        for item in manifest["instrumentation"]
        for event in item["events"]
    )

    verified = builder.verify_patch_set(PATCH_ROOT, manifest)
    assert verified["patch_set_sha256"] == manifest["patch_set_sha256"]
    assert verified["patches"]

    serialized = json.dumps(manifest, sort_keys=True)
    assert "/home/" not in serialized
    assert "/Users/" not in serialized
    assert "~" not in serialized


def test_patch_instruments_the_live_handshake_boundaries_not_only_from_wire():
    patch_text = (PATCH_ROOT / "0001-dwarf-measurement-instrumentation.patch").read_text(
        encoding="utf-8"
    )
    manifest = builder.load_manifest(PATCH_ROOT / "manifest.json")

    assert "diff --git a/crates/amaru-protocols/src/mux.rs" in patch_text
    assert "diff --git a/crates/amaru-protocols/src/handshake/responder.rs" in patch_text
    assert 'boundary = "mux-cbor-item"' in patch_text
    assert 'boundary = "mini-protocol-decode"' in patch_text
    assert "protocols::measurement::HANDSHAKE_NEGOTIATION" in patch_text
    assert "proto_id.opposite().to_string()" in patch_text
    assert "proto_id.to_string()" in patch_text

    changed = set(manifest["changed_paths"])
    assert "crates/amaru-protocols/src/mux.rs" in changed
    assert "crates/amaru-protocols/src/handshake/responder.rs" in changed
    protocol_capability = next(
        row
        for row in manifest["instrumentation"]
        if row["capability"] == "live-protocol-cbor-decode"
    )
    assert protocol_capability["events"] == [
        "amaru::protocols/measurement.protocol_ingress",
        "amaru::protocols/measurement.protocol_decode",
        "amaru::protocols/measurement.handshake_negotiation",
    ]


def test_source_validation_refuses_wrong_revision_and_dirty_tree(tmp_path):
    source, revision = _repository(tmp_path)
    assert builder.validate_clean_source(source, revision) == revision

    with pytest.raises(builder.BuildContractError, match="source revision"):
        builder.validate_clean_source(source, "0" * 40)

    (source / "tracked.txt").write_text("dirty\n")
    with pytest.raises(builder.BuildContractError, match="not clean"):
        builder.validate_clean_source(source, revision)


def test_preimage_validation_makes_patch_application_offset_free(tmp_path):
    source, _ = _repository(tmp_path)
    digest = hashlib.sha256((source / "tracked.txt").read_bytes()).hexdigest()
    preimages = [{"path": "tracked.txt", "sha256": digest}]

    assert builder.verify_preimages(source, preimages) == preimages
    (source / "tracked.txt").write_text("extra\noriginal\n")

    with pytest.raises(builder.BuildContractError, match="preimage digest"):
        builder.verify_preimages(source, preimages)


def test_clone_is_disposable_detached_exact_and_does_not_mutate_source(tmp_path):
    source, revision = _repository(tmp_path)
    destination = tmp_path / "build" / "source"

    clone = builder.clone_exact_source(source, destination, revision)

    assert clone == destination
    assert _git(clone, "rev-parse", "HEAD") == revision
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=clone,
        text=True,
        capture_output=True,
    )
    assert symbolic.returncode == 1
    assert symbolic.stdout == ""
    assert _git(source, "status", "--short") == ""
    with pytest.raises(builder.BuildContractError, match="already exists"):
        builder.clone_exact_source(source, destination, revision)


def test_logged_command_preserves_output_and_failure(tmp_path):
    log = tmp_path / "logs" / "build.log"
    result = builder.run_logged(
        ["sh", "-c", "printf 'stdout-line\\n'; printf 'stderr-line\\n' >&2"],
        cwd=tmp_path,
        log_path=log,
    )

    assert result.returncode == 0
    assert log.read_text() == "stdout-line\nstderr-line\n"
    assert builder.sha256_file(log) == hashlib.sha256(log.read_bytes()).hexdigest()

    with pytest.raises(builder.BuildContractError, match="command failed"):
        builder.run_logged(
            ["sh", "-c", "printf 'failed\\n'; exit 9"],
            cwd=tmp_path,
            log_path=tmp_path / "failure.log",
        )
    assert (tmp_path / "failure.log").read_text() == "failed\n"


def test_artifact_record_requires_real_file_and_retains_digest(tmp_path):
    binary = tmp_path / "amaru"
    binary.write_bytes(b"exact executable bytes")

    record = builder.artifact_record(binary, root=tmp_path)

    assert record == {
        "path": "amaru",
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "size_bytes": len(binary.read_bytes()),
    }
    with pytest.raises(builder.BuildContractError, match="missing artifact"):
        builder.artifact_record(tmp_path / "missing", root=tmp_path)


def test_target_registry_record_is_portable_and_exact(tmp_path):
    result = {
        "source": {"release": "10.11.20260912", "revision": REVISION},
        "patch_set_sha256": "f0e1aebca9adf2713d4d9f6f8ba33f20b0d04c3b35de6127d4a1e027a68b50af",
        "executable": {"sha256": "e" * 64},
        "image": {
            "status": "built",
            "reference": "dwarf/amaru-measurement:10.11.20260912-b159172",
            "image_id": "sha256:" + "f" * 64,
            "repo_digests": ["dwarf/amaru-measurement@sha256:" + "f" * 64],
            "smoke": {"status": "passed", "log": {"sha256": "a" * 64}},
            "runtime_probe": {
                "status": "passed",
                "image": "wrapper@sha256:" + "c" * 64,
                "log": {"sha256": "d" * 64},
            },
        },
        "result_sha256": "b" * 64,
    }

    record = builder.publish_target_record(result, registry_root=tmp_path)

    assert record["mode"] == "patched"
    assert record["source_revision"] == REVISION
    assert record["image_digest"] == "sha256:" + "f" * 64
    assert record["image_reference"] == "dwarf/amaru-measurement@sha256:" + "f" * 64
    assert record["executable_digest"] == "sha256:" + "e" * 64
    assert record["build_result_sha256"] == "sha256:" + "b" * 64
    assert record["runtime_probe_image"] == "wrapper@sha256:" + "c" * 64
    assert record["runtime_probe_log_sha256"] == "sha256:" + "d" * 64
    serialized = json.dumps(record, sort_keys=True)
    assert "/home/" not in serialized
    assert "/Users/" not in serialized


def test_target_registry_refuses_unbuilt_or_unsmoked_image(tmp_path):
    base = {
        "source": {"release": "10.11.20260912", "revision": REVISION},
        "patch_set_sha256": "f0e1aebca9adf2713d4d9f6f8ba33f20b0d04c3b35de6127d4a1e027a68b50af",
        "executable": {"sha256": "e" * 64},
        "image": {"status": "not-built"},
        "result_sha256": "b" * 64,
    }
    with pytest.raises(builder.BuildContractError, match="built image"):
        builder.publish_target_record(base, registry_root=tmp_path)

    base["image"] = {
        "status": "built",
        "reference": "dwarf/amaru-measurement:test",
        "image_id": "sha256:" + "f" * 64,
    }
    with pytest.raises(builder.BuildContractError, match="smoke"):
        builder.publish_target_record(base, registry_root=tmp_path)


def test_static_binary_contract_rejects_dynamic_elf(tmp_path, monkeypatch):
    binary = tmp_path / "amaru"
    binary.write_bytes(b"elf")

    def fake_run(command, **_kwargs):
        if command[0] == "file":
            return subprocess.CompletedProcess(command, 0, stdout="ELF, dynamically linked\n", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]\n",
            stderr="",
        )

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    with pytest.raises(builder.BuildContractError, match="statically linked"):
        builder.verify_static_binary(binary)
