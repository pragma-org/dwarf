"""Verified, fail-closed transfer of an already-qualified measurement target."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from profile_manager.deployment_versions import build_deployment_version_preview
from profile_manager.measurement_execution import (
    MeasurementExecutionError,
    prepare_scenario_measurements,
)
from profile_manager.profiles import Profile, deploy_command
from profile_manager.scenario import load_scenario
from scripts import transfer_measurement_target as transfer
from scripts.runtime_amaru_control_substrate import (
    build_runtime_metadata,
    validate_runtime_request,
)


REVISION = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
PATCH = "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6"
IMAGE_TAG = "dwarf/amaru-measurement:qualified"
CONFIG_BODY = json.dumps(
    {
        "architecture": "amd64",
        "os": "linux",
        "config": {
            "Labels": {
                "org.opencontainers.image.revision": REVISION,
                "org.dwarf.measurement.patch-sha256": PATCH,
            }
        },
    },
    separators=(",", ":"),
).encode()
CONFIG_DIGEST = "sha256:" + hashlib.sha256(CONFIG_BODY).hexdigest()
MANIFEST_BODY = json.dumps(
    {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": CONFIG_DIGEST,
            "size": len(CONFIG_BODY),
        },
        "layers": [],
    },
    separators=(",", ":"),
).encode()
MANIFEST_DIGEST = "sha256:" + hashlib.sha256(MANIFEST_BODY).hexdigest()
IMAGE_ID = MANIFEST_DIGEST
IMAGE_REF = "dwarf/amaru-measurement@" + MANIFEST_DIGEST
EXECUTABLE_DIGEST = "sha256:" + hashlib.sha256(
    b"qualified amaru executable"
).hexdigest()
PROBE_IMAGE = "example.invalid/bootstrap@sha256:" + "f" * 64


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class FakeDocker:
    def __init__(
        self,
        *,
        image_id: str = IMAGE_ID,
        platform: tuple[str, str] = ("linux", "amd64"),
        revision: str = REVISION,
        fail_run: bool = False,
        run_output: str = "amaru 10.11.20260918\n",
        probe_repo_digest: str = PROBE_IMAGE,
        loaded_image_id: str = CONFIG_DIGEST,
        reject_unresolved_source_reference: bool = False,
    ):
        self.calls: list[tuple[str, ...]] = []
        self.image_id = image_id
        self.platform = platform
        self.revision = revision
        self.fail_run = fail_run
        self.run_output = run_output
        self.probe_repo_digest = probe_repo_digest
        self.loaded_image_id = loaded_image_id
        self.reject_unresolved_source_reference = reject_unresolved_source_reference
        self.loaded = False

    def __call__(self, argv, *, output_path=None):
        args = tuple(str(part) for part in argv)
        self.calls.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            if args[-1] == PROBE_IMAGE:
                return json.dumps(
                    [
                        {
                            "Id": "sha256:" + "1" * 64,
                            "RepoDigests": [self.probe_repo_digest],
                            "Os": self.platform[0],
                            "Architecture": self.platform[1],
                            "Config": {"Labels": {}},
                        }
                    ]
                )
            return json.dumps(
                [
                    {
                        "Id": self.loaded_image_id if self.loaded else self.image_id,
                        "RepoDigests": [] if self.loaded else [IMAGE_REF],
                        "RepoTags": [IMAGE_TAG],
                        "Os": self.platform[0],
                        "Architecture": self.platform[1],
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.revision": self.revision,
                                "org.dwarf.measurement.patch-sha256": PATCH,
                            }
                        },
                    }
                ]
            )
        if args[:3] == ("docker", "image", "save"):
            assert output_path is not None
            with tarfile.open(output_path, "w") as archive:
                payloads = {
                    "blobs/sha256/" + CONFIG_DIGEST[7:]: CONFIG_BODY,
                    "blobs/sha256/" + MANIFEST_DIGEST[7:]: MANIFEST_BODY,
                    "index.json": json.dumps(
                        {
                            "schemaVersion": 2,
                            "mediaType": "application/vnd.oci.image.index.v1+json",
                            "manifests": [
                                {
                                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                                    "digest": MANIFEST_DIGEST,
                                    "size": len(MANIFEST_BODY),
                                }
                            ],
                        }
                    ).encode(),
                    "manifest.json": json.dumps(
                        [
                            {
                                "Config": "blobs/sha256/" + CONFIG_DIGEST[7:],
                                "RepoTags": [IMAGE_TAG],
                                "Layers": [],
                            }
                        ]
                    ).encode(),
                    "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
                }
                for name, body in payloads.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(body)
                    archive.addfile(info, io.BytesIO(body))
            return ""
        if args[:2] == ("docker", "load"):
            self.loaded = True
            return "Loaded image\n"
        if args[:2] == ("docker", "run"):
            if (
                self.loaded
                and self.reject_unresolved_source_reference
                and IMAGE_REF in args
            ):
                raise transfer.TransferError(
                    "source repository digest is not resolvable after classic-Docker load"
                )
            if self.fail_run:
                raise transfer.TransferError("probe failed")
            if "sha256sum" in args:
                return EXECUTABLE_DIGEST[7:] + "  /usr/local/bin/amaru\n"
            return self.run_output
        raise AssertionError(f"unexpected Docker command: {args}")


def _qualified_tree(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "qualified"
    (root / "artifacts").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "evidence").mkdir()
    (root / "work" / "source").mkdir(parents=True)
    (root / "work" / "source" / "not-transfer-evidence").write_text("private checkout\n")
    executable = root / "artifacts" / "amaru"
    executable.write_bytes(b"qualified amaru executable")
    smoke = root / "logs" / "image-smoke.log"
    smoke.write_text("amaru 10.11.20260918\n")
    probe = root / "logs" / "bootstrap-wrapper-smoke.log"
    probe.write_text("amaru 10.11.20260918\n")
    (root / "logs" / "nested").mkdir()
    (root / "logs" / "nested" / "manifest.json").write_text("retained evidence\n")
    build_result = {
        "schema_version": 1,
        "created_at": "2026-10-10T00:00:00Z",
        "source": {"release": "10.11.20260918", "revision": REVISION},
        "patch_set_sha256": PATCH,
        "build": {
            "log": {
                "path": "logs/cargo-build.log",
                "sha256": "",
                "size_bytes": 0,
            }
        },
        "executable": {
            "path": "artifacts/amaru",
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "size_bytes": executable.stat().st_size,
        },
        "image": {
            "status": "built",
            "image_id": IMAGE_ID,
            "reference": IMAGE_TAG,
            "repo_digests": [IMAGE_REF],
            "build_log": {
                "path": "logs/docker-build.log",
                "sha256": "",
                "size_bytes": 0,
            },
            "smoke": {
                "status": "passed",
                "log": {"path": "logs/image-smoke.log", "sha256": _sha(smoke)[7:]},
            },
            "runtime_probe": {
                "status": "passed",
                "image": PROBE_IMAGE,
                "log": {
                    "path": "logs/bootstrap-wrapper-smoke.log",
                    "sha256": _sha(probe)[7:],
                },
            },
        },
    }
    for field, content in (
        (build_result["build"]["log"], "compiler output\n"),
        (build_result["image"]["build_log"], "docker output\n"),
    ):
        path = root / field["path"]
        path.write_text(content)
        field["sha256"] = _sha(path)[7:]
        field["size_bytes"] = path.stat().st_size
    result_path = root / "evidence" / "build-result.json"
    result_path.write_text(json.dumps(build_result, indent=2, sort_keys=True) + "\n")
    record = {
        "schema_version": 1,
        "implementation": "amaru",
        "mode": "patched",
        "version": "10.11.20260918",
        "source_revision": REVISION,
        "patch_set_sha256": PATCH,
        "image_reference": IMAGE_REF,
        "image_digest": IMAGE_ID,
        "executable_digest": _sha(executable),
        "build_result_sha256": _sha(result_path),
        "smoke_log_sha256": _sha(smoke),
        "runtime_probe_image": PROBE_IMAGE,
        "runtime_probe_log_sha256": _sha(probe),
        "created_at": "2026-10-10T00:00:00Z",
    }
    registry = tmp_path / "source-registry"
    record_path = registry / "amaru" / REVISION / f"{PATCH}.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return registry, root, record


def _export(registry, build_root, bundle, runner):
    return transfer.export_bundle(
        registry,
        build_root,
        bundle,
        source_revision=REVISION,
        patch_set_sha256=PATCH,
        source_installation_id="install-source-test",
        runner=runner,
    )


def _reseal_manifest(bundle: Path) -> None:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = [
        {
            "path": path.relative_to(bundle).as_posix(),
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(bundle.rglob("*"))
        if path.is_file() and path != manifest_path
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def test_export_manifest_covers_every_payload_file_and_exact_identity(tmp_path):
    registry, build_root, record = _qualified_tree(tmp_path)
    bundle = tmp_path / "bundle"
    docker = FakeDocker()

    result = _export(registry, build_root, bundle, docker)

    manifest = json.loads((bundle / "manifest.json").read_text())
    actual = sorted(
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path != bundle / "manifest.json"
    )
    assert [entry["path"] for entry in manifest["files"]] == actual
    assert "qualification/logs/nested/manifest.json" in actual
    assert manifest["identity"] == {
        key: record[key]
        for key in ("implementation", "version", "source_revision", "patch_set_sha256", "image_reference", "image_digest")
    }
    assert result["bundle_sha256"].startswith("sha256:")
    assert manifest["source"] == {
        "installation_id": "install-source-test",
        "record": f"amaru/{REVISION}/{PATCH}.json",
    }
    assert (bundle / "image.tar").is_file()
    assert manifest["archive"] == {
        "config_digest": CONFIG_DIGEST,
        "manifest_digest": MANIFEST_DIGEST,
        "platform": "linux/amd64",
        "source_tag": IMAGE_TAG,
    }
    assert not (bundle / "qualification" / "work").exists()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("payload", "checksum"),
        ("manifest_escape", "canonical relative path"),
        ("wrong_image", "image config digest"),
        ("wrong_platform", "linux/amd64"),
    ],
)
def test_import_rejects_untrusted_bundle_without_registry_write(tmp_path, mutation, match):
    registry_source, build_root, record = _qualified_tree(tmp_path)
    bundle = tmp_path / "bundle"
    _export(registry_source, build_root, bundle, FakeDocker())
    docker = FakeDocker()
    if mutation == "payload":
        (bundle / "qualification" / "logs" / "image-smoke.log").write_text("tampered\n")
    elif mutation == "manifest_escape":
        manifest = json.loads((bundle / "manifest.json").read_text())
        manifest["files"][0]["path"] = "../escape"
        (bundle / "manifest.json").write_text(json.dumps(manifest))
    elif mutation == "wrong_image":
        docker.loaded_image_id = "sha256:" + "9" * 64
    elif mutation == "wrong_platform":
        docker.platform = ("linux", "arm64")

    registry = tmp_path / "registry"
    registry.mkdir()
    with pytest.raises(transfer.TransferError, match=match):
        transfer.import_bundle(bundle, registry, runner=docker)

    assert not registry.exists() or not any(registry.rglob("*.json"))


def test_import_revalidates_smoke_probe_and_publishes_atomically(tmp_path):
    registry_source, build_root, record = _qualified_tree(tmp_path)
    bundle = tmp_path / "bundle"
    _export(registry_source, build_root, bundle, FakeDocker())
    registry = tmp_path / "registry"
    registry.mkdir()
    docker = FakeDocker()

    installed = transfer.import_bundle(bundle, registry, runner=docker)

    destination = registry / "amaru" / REVISION / f"{PATCH}.json"
    published = json.loads(destination.read_text())
    assert {
        key: published[key]
        for key in record
        if key != "image_digest"
    } == {
        key: record[key]
        for key in record
        if key != "image_digest"
    }
    assert published["qualification_image_id"] == record["image_digest"]
    assert published["image_manifest_digest"] == MANIFEST_DIGEST
    assert published["image_config_digest"] == CONFIG_DIGEST
    assert published["runtime_image_reference"] == CONFIG_DIGEST
    assert published["image_digest"] == CONFIG_DIGEST
    assert published["transfer_source_installation_id"] == "install-source-test"
    assert published["transfer_manifest_sha256"].startswith("sha256:")
    assert installed["record_path"] == str(destination)
    assert any(call[:2] == ("docker", "load") for call in docker.calls)
    assert sum(call[:2] == ("docker", "run") for call in docker.calls) == 3
    assert sum(call[:3] == ("docker", "image", "inspect") for call in docker.calls) == 2
    assert not list(destination.parent.glob("*.tmp"))

    installed_again = transfer.import_bundle(bundle, registry, runner=FakeDocker())
    assert installed_again["record_path"] == str(destination)


def test_imported_real_oci_identity_reaches_runtime_and_patched_measurements(
    tmp_path, monkeypatch
):
    source_registry, build_root, source_record = _qualified_tree(tmp_path)
    source_result = build_root / "evidence" / "build-result.json"
    source_result_bytes = source_result.read_bytes()
    source_result_sha256 = _sha(source_result)
    bundle = tmp_path / "bundle"
    _export(source_registry, build_root, bundle, FakeDocker())
    bundled_result = bundle / "qualification" / "evidence" / "build-result.json"
    destination_registry = tmp_path / "destination-registry"
    destination_registry.mkdir()
    destination_docker = FakeDocker(
        loaded_image_id=CONFIG_DIGEST,
        reject_unresolved_source_reference=True,
    )

    transfer.import_bundle(
        bundle,
        destination_registry,
        runner=destination_docker,
    )

    destination_record_path = (
        destination_registry / "amaru" / REVISION / f"{PATCH}.json"
    )
    destination_record = json.loads(destination_record_path.read_text())
    assert source_record["image_digest"] == MANIFEST_DIGEST
    assert destination_record["qualification_image_id"] == MANIFEST_DIGEST
    assert destination_record["image_manifest_digest"] == MANIFEST_DIGEST
    assert destination_record["image_config_digest"] == CONFIG_DIGEST
    assert destination_record["runtime_image_reference"] == CONFIG_DIGEST
    assert destination_record["image_digest"] == CONFIG_DIGEST
    assert MANIFEST_DIGEST != CONFIG_DIGEST
    assert source_result.read_bytes() == source_result_bytes
    assert bundled_result.read_bytes() == source_result_bytes
    assert destination_record["build_result_sha256"] == source_result_sha256
    target_runs = [
        call
        for call in destination_docker.calls
        if call[:2] == ("docker", "run") and PROBE_IMAGE not in call
    ]
    assert len(target_runs) == 2
    assert all(CONFIG_DIGEST in call for call in target_runs)
    assert all(IMAGE_REF not in call for call in target_runs)

    monkeypatch.setenv(
        "ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(destination_registry)
    )
    profile_data = json.loads(
        Path(
            "dwarf/profiles/profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3/profile.yaml"
        ).read_text()
    )
    profile = Profile.from_dict(profile_data)
    preview = build_deployment_version_preview(profile_data)
    command = deploy_command(profile, version_preview=preview)
    config_text = command.split(
        "cat > \"$config_path\" <<'DWARF_VERSIONED_DEPLOYMENT'\n", 1
    )[1].split("\nDWARF_VERSIONED_DEPLOYMENT\n", 1)[0]
    config = json.loads(config_text)

    validate_runtime_request(config)
    target = config["measurement_target_identity"]
    assert config["amaru_image"] == CONFIG_DIGEST
    assert target["image"] == CONFIG_DIGEST
    assert target["image_digest"] == CONFIG_DIGEST
    assert target["image_config_digest"] == CONFIG_DIGEST
    assert target["image_manifest_digest"] == MANIFEST_DIGEST
    assert target["source_image_reference"] == IMAGE_REF
    assert target["transfer_source_installation_id"] == destination_record[
        "transfer_source_installation_id"
    ]

    service_identity = {
        "container": "dwarf-profile-zb-amaru-relay-1",
        "matched": True,
        "expected_image": CONFIG_DIGEST,
        "expected_image_id": CONFIG_DIGEST,
        "artifact_image_id": CONFIG_DIGEST,
        "executable_digest_reported": EXECUTABLE_DIGEST,
        "executable_digest_matched": True,
    }
    runtime = build_runtime_metadata(
        config,
        identity={
            "matched": True,
            "services": {"amaru-relay-1": service_identity},
        },
        observation={"state": "healthy"},
        compose_file="/var/dwarf/runtime/docker-compose.json",
    )
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    scenario = load_scenario(
        "dwarf/scenarios/amaru-measurement-boundary-proof-patched-20260918-mixed-1112.yaml"
    )

    prepared = prepare_scenario_measurements(
        scenario,
        runtime_metadata_path=runtime_path,
        tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
    )

    resolved_identity = prepared.resolution["target_identity"]
    assert resolved_identity["image_reference"] == CONFIG_DIGEST
    assert resolved_identity["image_digest"] == CONFIG_DIGEST
    assert resolved_identity["image_manifest_digest"] == MANIFEST_DIGEST
    assert resolved_identity["image_config_digest"] == CONFIG_DIGEST
    assert resolved_identity["qualification_image_id"] == MANIFEST_DIGEST
    assert resolved_identity["transfer_manifest_sha256"] == destination_record[
        "transfer_manifest_sha256"
    ]
    assert resolved_identity["source_image_reference"] == IMAGE_REF
    assert resolved_identity["transfer_source_installation_id"] == destination_record[
        "transfer_source_installation_id"
    ]
    assert any(
        item["id"].startswith("amaru-patched-")
        for item in prepared.resolution["resolved"]
    )

    runtime["identity"]["services"]["amaru-relay-1"][
        "artifact_image_id"
    ] = MANIFEST_DIGEST
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    with pytest.raises(MeasurementExecutionError, match="image config identity"):
        prepare_scenario_measurements(
            scenario,
            runtime_metadata_path=runtime_path,
            tip_probe=lambda: {"block_height": 1, "block_hash": "a" * 64},
        )


def test_import_rejects_mismatched_existing_record(tmp_path):
    registry_source, build_root, _ = _qualified_tree(tmp_path)
    bundle = tmp_path / "bundle"
    _export(registry_source, build_root, bundle, FakeDocker())
    registry = tmp_path / "registry"
    registry.mkdir()
    transfer.import_bundle(bundle, registry, runner=FakeDocker())
    destination = registry / "amaru" / REVISION / f"{PATCH}.json"
    existing = json.loads(destination.read_text())
    existing["version"] = "different"
    destination.write_text(json.dumps(existing))

    with pytest.raises(transfer.TransferError, match="refusing to overwrite"):
        transfer.import_bundle(bundle, registry, runner=FakeDocker())

    assert json.loads(destination.read_text())["version"] == "different"


def test_import_rejects_resealed_identity_path_escape(tmp_path):
    registry_source, build_root, _ = _qualified_tree(tmp_path)
    bundle = tmp_path / "bundle"
    _export(registry_source, build_root, bundle, FakeDocker())
    result_path = bundle / "qualification" / "evidence" / "build-result.json"
    result = json.loads(result_path.read_text())
    result["source"]["revision"] = "../escape"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    record_path = bundle / "target-record.json"
    record = json.loads(record_path.read_text())
    record["source_revision"] = "../escape"
    record["build_result_sha256"] = _sha(result_path)
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["identity"]["source_revision"] = "../escape"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    _reseal_manifest(bundle)
    registry = tmp_path / "registry"
    registry.mkdir()

    with pytest.raises(transfer.TransferError, match="exact SHA-40"):
        transfer.import_bundle(bundle, registry, runner=FakeDocker(revision="../escape"))

    assert not any(registry.rglob("*.json"))


@pytest.mark.parametrize(
    "docker",
    [
        FakeDocker(revision="9" * 40),
        FakeDocker(fail_run=True),
        FakeDocker(run_output="amaru wrong-version\n"),
        FakeDocker(probe_repo_digest="example.invalid/wrong@sha256:" + "2" * 64),
    ],
    ids=[
        "wrong-oci-label",
        "failed-live-probe",
        "wrong-live-version",
        "wrong-probe-image",
    ],
)
def test_import_rejects_destination_verification_failure_before_publish(
    tmp_path, docker
):
    registry_source, build_root, _ = _qualified_tree(tmp_path)
    bundle = tmp_path / "bundle"
    _export(registry_source, build_root, bundle, FakeDocker())
    registry = tmp_path / "registry"
    registry.mkdir()

    with pytest.raises(transfer.TransferError):
        transfer.import_bundle(bundle, registry, runner=docker)

    assert not registry.exists() or not any(registry.rglob("*.json"))


def test_import_rejects_modified_executable_even_with_rewritten_manifest(tmp_path):
    registry_source, build_root, _ = _qualified_tree(tmp_path)
    bundle = tmp_path / "bundle"
    _export(registry_source, build_root, bundle, FakeDocker())
    executable = bundle / "qualification" / "artifacts" / "amaru"
    executable.write_bytes(b"replacement executable")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entry = next(
        item
        for item in manifest["files"]
        if item["path"] == "qualification/artifacts/amaru"
    )
    entry["sha256"] = _sha(executable)
    entry["size_bytes"] = executable.stat().st_size
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    registry = tmp_path / "registry"
    registry.mkdir()

    with pytest.raises(transfer.TransferError, match="executable checksum"):
        transfer.import_bundle(bundle, registry, runner=FakeDocker())

    assert not registry.exists() or not any(registry.rglob("*.json"))


def test_import_detects_tampered_nested_manifest_file(tmp_path):
    registry_source, build_root, _ = _qualified_tree(tmp_path)
    bundle = tmp_path / "bundle"
    _export(registry_source, build_root, bundle, FakeDocker())
    nested = bundle / "qualification" / "logs" / "nested" / "manifest.json"
    nested.write_text("tampered evidence\n")
    registry = tmp_path / "registry"
    registry.mkdir()

    with pytest.raises(transfer.TransferError, match="checksum"):
        transfer.import_bundle(bundle, registry, runner=FakeDocker())

    assert not any(registry.rglob("*.json"))
