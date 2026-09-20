import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import build_cardano_measurement_target as builder
from profile_manager.measurement_targets import (
    MeasurementTargetError,
    resolve_patched_cardano_target,
)


REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
PATCH_ROOT = Path("dwarf/targets/cardano-node/measurement-patches") / REVISION


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "fixture@example.invalid")
    _git(source, "config", "user.name", "fixture")
    (source / "tracked.txt").write_text("original\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-q", "-m", "fixture")
    return source, _git(source, "rev-parse", "HEAD")


def test_manifest_pins_cardano_source_dependency_toolchain_and_base_image():
    manifest = builder.load_manifest(PATCH_ROOT / "manifest.json")

    assert manifest["source"] == {
        "repository": "https://github.com/IntersectMBO/cardano-node.git",
        "release": "11.1.2",
        "tag": "11.1.2",
        "revision": REVISION,
    }
    assert manifest["toolchain"] == {"ghc": "9.6.7", "cabal": "3.16.0.0"}
    assert manifest["base_image"].endswith(
        "@sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f"
    )
    dependencies = {row["package"]: row for row in manifest["dependencies"]}
    assert set(dependencies) == {
        "ouroboros-network", "ouroboros-consensus", "cardano-ledger-alonzo"
    }
    assert dependencies["ouroboros-network"]["version"] == "1.2.0.0"
    assert dependencies["ouroboros-consensus"]["version"] == "4.2.1.0"
    assert dependencies["cardano-ledger-alonzo"]["version"] == "1.16.0.0"
    assert {row["capability"] for row in manifest["instrumentation"]} == {
        "live-protocol-receive-decode",
        "live-block-application-and-epoch-transition",
        "live-plutus-vm",
    }
    assert all(row["all_outcomes_timed"] for row in manifest["instrumentation"])
    assert "--builddir=dist-newstyle-dwarf-measurement" in manifest["build"]["command"]
    assert "--builddir=dist-newstyle-dwarf-measurement" in manifest["build"]["binary_query"]
    assert "CABAL_BUILDDIR" not in manifest["build"]["environment"]
    verified = builder.verify_patch_set(PATCH_ROOT, manifest)
    assert verified["patch_set_sha256"] == manifest["patch_set_sha256"]
    assert len(verified["patches"]) == 3


def test_source_validation_refuses_wrong_revision_and_dirty_tree(tmp_path):
    source, revision = _repository(tmp_path)
    assert builder.validate_clean_source(source, revision) == revision
    with pytest.raises(builder.BuildContractError, match="source revision"):
        builder.validate_clean_source(source, "0" * 40)
    (source / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(builder.BuildContractError, match="not clean"):
        builder.validate_clean_source(source, revision)


def test_dependency_archive_digest_is_fail_closed(tmp_path):
    archive = tmp_path / "dependency.tar.gz"
    archive.write_bytes(b"exact dependency archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert builder.verify_dependency_archive(archive, digest) == digest
    with pytest.raises(builder.BuildContractError, match="dependency archive digest"):
        builder.verify_dependency_archive(archive, "0" * 64)


def test_preimage_validation_refuses_offsets(tmp_path):
    root = tmp_path / "dependency"
    path = root / "file.hs"
    path.parent.mkdir()
    path.write_text("exact\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    preimages = [{"path": "file.hs", "sha256": digest}]
    assert builder.verify_preimages(root, preimages) == preimages
    path.write_text("offset\nexact\n", encoding="utf-8")
    with pytest.raises(builder.BuildContractError, match="preimage digest"):
        builder.verify_preimages(root, preimages)


def test_dependency_patch_is_applied_inside_untracked_vendor_root(tmp_path):
    source, _ = _repository(tmp_path)
    dependency = source / "vendor" / "example-1.0"
    dependency.mkdir(parents=True)
    target = dependency / "file.txt"
    target.write_text("before\n", encoding="utf-8")
    patch = tmp_path / "change.patch"
    patch.write_text(
        "diff --git a/file.txt b/file.txt\n"
        "index 94b334d..5b41a55 100644\n"
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n",
        encoding="utf-8",
    )

    builder.apply_patch_to_dependency(
        dependency,
        patch,
        check_log=tmp_path / "check.log",
        apply_log=tmp_path / "apply.log",
    )

    assert target.read_text(encoding="utf-8") == "after\n"
    assert "file.txt" not in _git(source, "status", "--short")


def test_dynamic_runtime_libraries_are_staged_in_an_isolated_runtime_root(tmp_path, monkeypatch):
    first = tmp_path / "lib" / "libexample.so.1"
    loader = tmp_path / "lib64" / "ld-linux-x86-64.so.2"
    first.parent.mkdir(parents=True)
    loader.parent.mkdir(parents=True)
    first.write_bytes(b"example-library")
    loader.write_bytes(b"example-loader")
    output = (
        f"libexample.so.1 => {first} (0x1)\n"
        f"{loader} (0x2)\n"
    )
    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output, stderr=""),
    )

    discovered = builder.discover_runtime_libraries(tmp_path / "cardano-node")
    staged = builder.stage_runtime_libraries(
        discovered, context=tmp_path / "context"
    )

    assert discovered == [first, loader]
    assert (tmp_path / "context" / "runtime-root" / first.relative_to("/")).read_bytes() == b"example-library"
    assert (tmp_path / "context" / "runtime-root" / loader.relative_to("/")).read_bytes() == b"example-loader"
    assert {row["source_path"] for row in staged} == {str(first), str(loader)}
    assert {row["install_path"] for row in staged} == {
        "/opt/dwarf-cardano-runtime" + str(first),
        "/opt/dwarf-cardano-runtime" + str(loader),
    }


def test_measurement_image_keeps_packaged_libraries_isolated_from_base_image_tools():
    body = builder.measurement_dockerfile(
        "example.invalid/cardano@sha256:" + "a" * 64,
        source_revision=REVISION,
        patch_set_sha256="b" * 64,
    )
    assert "LD_LIBRARY_PATH" not in body
    assert "RUN " not in body
    assert "COPY runtime-root/ /opt/dwarf-cardano-runtime/" in body
    assert "COPY --chmod=0755 cardano-node /opt/dwarf-cardano-runtime/bin/cardano-node" in body
    assert "COPY --chmod=0755 cardano-node-wrapper /usr/local/bin/cardano-node" in body
    assert f'org.opencontainers.image.revision="{REVISION}"' in body
    assert f'org.dwarf.measurement.patch-sha256="{"b" * 64}"' in body

    wrapper = builder.measurement_wrapper()
    assert "/opt/dwarf-cardano-runtime/lib64/ld-linux-x86-64.so.2" in wrapper
    assert "--library-path" in wrapper
    assert "/opt/dwarf-cardano-runtime/bin/cardano-node" in wrapper


def test_registry_record_requires_smoked_immutable_image(tmp_path):
    result = {
        "source": {"release": "11.1.2", "revision": REVISION},
        "patch_set_sha256": "a" * 64,
        "executable": {"sha256": "e" * 64},
        "build_result_sha256": "b" * 64,
        "image": {
            "status": "built",
            "reference": "dwarf/cardano-measurement:11.1.2-fef83fed",
            "image_id": "sha256:" + "f" * 64,
            "repo_digests": ["dwarf/cardano-measurement@sha256:" + "f" * 64],
            "smoke": {"status": "passed", "log": {"sha256": "c" * 64}},
            "runtime_probe": {
                "status": "passed",
                "log": {"sha256": "d" * 64},
            },
        },
    }
    record = builder.publish_target_record(result, registry_root=tmp_path)
    assert record["implementation"] == "cardano-node"
    assert record["mode"] == "patched"
    assert record["source_revision"] == REVISION
    assert record["image_digest"] == "sha256:" + "f" * 64
    assert record["runtime_probe_log_sha256"] == "sha256:" + "d" * 64
    assert "/home/" not in json.dumps(record)

    bad = dict(result)
    bad["image"] = {"status": "not-built"}
    with pytest.raises(builder.BuildContractError, match="built image"):
        builder.publish_target_record(bad, registry_root=tmp_path)

    no_probe = json.loads(json.dumps(result))
    del no_probe["image"]["runtime_probe"]
    with pytest.raises(builder.BuildContractError, match="runtime probe"):
        builder.publish_target_record(no_probe, registry_root=tmp_path)


def test_cardano_target_resolver_requires_exact_profile_and_runtime_probe(tmp_path):
    profile = SimpleNamespace(
        cardano_version="11.1.2",
        measurement_patch_revision=REVISION,
        measurement_patch_set_sha256="a" * 64,
    )
    path = tmp_path / "cardano-node" / REVISION / ("a" * 64 + ".json")
    path.parent.mkdir(parents=True)
    record = {
        "schema_version": 1,
        "implementation": "cardano-node",
        "version": "11.1.2",
        "source_revision": REVISION,
        "mode": "patched",
        "patch_set_sha256": "a" * 64,
        "image_reference": "dwarf/cardano-measurement@sha256:" + "b" * 64,
        "image_digest": "sha256:" + "b" * 64,
        "executable_digest": "sha256:" + "c" * 64,
        "build_result_sha256": "sha256:" + "d" * 64,
        "runtime_probe_log_sha256": "sha256:" + "e" * 64,
    }
    path.write_text(json.dumps(record))
    assert resolve_patched_cardano_target(profile, registry_root=tmp_path) == record
    del record["runtime_probe_log_sha256"]
    path.write_text(json.dumps(record))
    with pytest.raises(MeasurementTargetError, match="runtime probe"):
        resolve_patched_cardano_target(profile, registry_root=tmp_path)


NANOSECOND_PATCH_ROOT = (
    Path("dwarf/targets/cardano-node/measurement-patches-nanoseconds-v2") / REVISION
)


def test_nanosecond_v2_manifest_is_additive_and_v1_stays_byte_exact():
    assert builder.sha256_file(PATCH_ROOT / "manifest.json") == (
        "6ec8329317dec47205504d8511e9a3584f5a1fcd0bf826f59165af7f9c669aa9"
    )
    expected_v1_patches = {
        "0001-network-protocol-and-ledger-measurements.patch": "14e912d63297a4ffc429cac3295d90655a12330a6ee7a294823b2a8c67715069",
        "0002-consensus-block-epoch-measurements.patch": "cc05a427560af75479ca9c0ba923b8698c1b09e6bef5ddf4ab68bf33eaac52b4",
        "0003-plutus-vm-measurement.patch": "aec5bbc3a4de61b059f86d99dad207746f67f8c4fdffe1c74623c4dfa2507289",
    }
    for name, digest in expected_v1_patches.items():
        assert builder.sha256_file(PATCH_ROOT / name) == digest

    manifest = builder.load_manifest(NANOSECOND_PATCH_ROOT / "manifest.json")
    assert manifest["measurement_revision"] == "nanoseconds-v2"
    assert manifest["source"]["revision"] == REVISION
    verified = builder.verify_patch_set(NANOSECOND_PATCH_ROOT, manifest)
    assert verified["patch_set_sha256"] == manifest["patch_set_sha256"]
    assert len(verified["patches"]) == 3


def test_nanosecond_v2_patches_keep_duration_us_and_add_elapsed_nanos():
    manifest = builder.load_manifest(NANOSECOND_PATCH_ROOT / "manifest.json")
    patch_text = "\n".join(
        (NANOSECOND_PATCH_ROOT / item["path"]).read_text(encoding="utf-8")
        for item in manifest["patches"]
    )

    assert patch_text.count('"elapsed_nanos" .=') >= 3
    assert patch_text.count('"duration_us" .=') >= 3
    assert "emitDwarfProtocolMeasurement" in patch_text
    assert 'measureDwarfEither "block-application"' in patch_text
    assert 'measureDwarfPure "epoch-transition"' in patch_text
    assert 'stage" .= ("plutus-vm"' in patch_text
    assert "`div` 1000" in patch_text
