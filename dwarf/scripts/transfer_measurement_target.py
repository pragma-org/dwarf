"""Export and import a fully verified DWARF measurement-target bundle.

This is deliberately a transfer path, not a second qualification path. The
source build result, its evidence, and the immutable Docker image must already
agree. Import repeats those checks and publishes the ordinary registry record
only after the destination can execute both target probes.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any, Callable, Mapping, Sequence

from profile_manager.measurement_targets import (
    MeasurementTargetError,
    plutus_adapter_executable_platform,
)


BUNDLE_KIND = "dwarf-measurement-target-transfer"
CONFORMANCE_BUNDLE_KIND = "dwarf-plutus-conformance-adapter-transfer"
CBOR_CONFORMANCE_BUNDLE_KIND = "dwarf-cbor-conformance-adapter-transfer"
DIGEST_PREFIX = "sha256:"
QUALIFICATION_DIRS = ("artifacts", "evidence", "logs")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_REFERENCE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
INSTALLATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_CONFORMANCE_SPECS = {
    "plutus": {
        "registry_dir": "plutus-conformance",
        "record_kind": "production-plutus-v2-conformance",
        "measurement_boundary": "production-plutus-v2-vm-only",
        "manifest_name": "plutus-manifest.json",
        "bundle_kind": CONFORMANCE_BUNDLE_KIND,
        "label": "Plutus",
    },
    "cbor": {
        "registry_dir": "conformance",
        "record_kind": "production-cbor-conformance",
        "measurement_boundary": "production-codec-only",
        "manifest_name": "manifest.json",
        "bundle_kind": CBOR_CONFORMANCE_BUNDLE_KIND,
        "label": "CBOR",
    },
}


def _conformance_spec(family: str) -> Mapping[str, str]:
    try:
        return _CONFORMANCE_SPECS[family]
    except KeyError as error:
        raise TransferError("unsupported conformance adapter family") from error


class TransferError(ValueError):
    """The transfer bundle failed a trust or identity boundary."""


Runner = Callable[..., str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return DIGEST_PREFIX + digest.hexdigest()


def _digest(value: Any) -> str:
    text = str(value or "")
    return text if text.startswith(DIGEST_PREFIX) else DIGEST_PREFIX + text


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TransferError(f"cannot read {label}: {error}") from error
    if not isinstance(value, dict):
        raise TransferError(f"{label} is not a JSON object")
    return value


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    text = str(value or "")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts or path.as_posix() != text:
        raise TransferError(f"{label} is not a canonical relative path")
    return path


def _within(root: Path, relative: Any, label: str) -> Path:
    rel = _safe_relative(relative, label)
    candidate = root.joinpath(*rel.parts)
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise TransferError(f"{label} escapes the qualification root") from error
    if not candidate.is_file() or candidate.is_symlink():
        raise TransferError(f"{label} is not a regular file")
    return candidate


def _require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise TransferError(
            f"{label} mismatch: expected {expected!r}, got {actual!r}"
        )


def _validate_identity(record: Mapping[str, Any]) -> None:
    if record.get("schema_version") != 1:
        raise TransferError("target record is not schema version 1")
    for key, expected in (("implementation", "amaru"), ("mode", "patched")):
        _require_equal(record.get(key), expected, f"target {key}")
    if not str(record.get("version") or ""):
        raise TransferError("target version is absent")
    if not SHA40.fullmatch(str(record.get("source_revision") or "")):
        raise TransferError("target source revision is not an exact SHA-40")
    if not SHA64.fullmatch(str(record.get("patch_set_sha256") or "")):
        raise TransferError("target patch set is not an exact SHA-256")
    if not IMAGE_REFERENCE.fullmatch(str(record.get("image_reference") or "")):
        raise TransferError("target image reference is not immutable")
    if not IMAGE_REFERENCE.fullmatch(str(record.get("runtime_probe_image") or "")):
        raise TransferError("runtime probe image is not immutable")
    for key in (
        "image_digest",
        "executable_digest",
        "build_result_sha256",
        "smoke_log_sha256",
        "runtime_probe_log_sha256",
    ):
        if not DIGEST.fullmatch(str(record.get(key) or "")):
            raise TransferError(f"target {key} is not an exact SHA-256 digest")


def _validated_registry_root(value: str | Path) -> Path:
    root = Path(value)
    if not root.is_dir() or root.is_symlink():
        raise TransferError("measurement-target registry root must be an existing non-symlink directory")
    return root.resolve(strict=True)


def _record_path(
    registry_root: str | Path, source_revision: str, patch_set_sha256: str
) -> Path:
    if not SHA40.fullmatch(source_revision):
        raise TransferError("target source revision is not an exact SHA-40")
    if not SHA64.fullmatch(patch_set_sha256):
        raise TransferError("target patch set is not an exact SHA-256")
    root = _validated_registry_root(registry_root)
    path = root / "amaru" / source_revision / f"{patch_set_sha256}.json"
    try:
        path.parent.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise TransferError("target record escapes the measurement-target registry") from error
    return path


def _validate_tree(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise TransferError("qualification root is not a regular directory")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise TransferError(
                f"qualification tree contains a symlink: {path.relative_to(root)}"
            )
        if not path.is_file() and not path.is_dir():
            raise TransferError(
                f"qualification tree contains a special file: {path.relative_to(root)}"
            )


def _validate_record_and_result(
    record: Mapping[str, Any], build_root: Path
) -> tuple[dict[str, Any], Path, dict[str, str]]:
    _validate_identity(record)

    if not build_root.is_dir() or build_root.is_symlink():
        raise TransferError("qualification root is not a regular directory")
    for dirname in QUALIFICATION_DIRS:
        directory = build_root / dirname
        if directory.exists():
            _validate_tree(directory)
    result_path = build_root / "evidence" / "build-result.json"
    if not result_path.is_file() or result_path.is_symlink():
        raise TransferError("qualification build-result evidence is absent")
    _require_equal(
        _sha256(result_path),
        record.get("build_result_sha256"),
        "build-result checksum",
    )
    result = _load_object(result_path, "build result")
    source = result.get("source")
    executable = result.get("executable")
    image = result.get("image")
    if not all(
        isinstance(value, Mapping) for value in (source, executable, image)
    ):
        raise TransferError(
            "build result is missing source, executable, or image evidence"
        )
    assert isinstance(source, Mapping)
    assert isinstance(executable, Mapping)
    assert isinstance(image, Mapping)
    for actual, expected, label in (
        (source.get("release"), record.get("version"), "qualified version"),
        (
            source.get("revision"),
            record.get("source_revision"),
            "qualified source revision",
        ),
        (
            result.get("patch_set_sha256"),
            record.get("patch_set_sha256"),
            "qualified patch set",
        ),
        (
            image.get("image_id"),
            record.get("image_digest"),
            "qualified image config digest",
        ),
    ):
        _require_equal(actual, expected, label)
    repo_digests = image.get("repo_digests")
    if (
        not isinstance(repo_digests, list)
        or record.get("image_reference") not in repo_digests
    ):
        raise TransferError("qualified image repository digest mismatch")
    executable_path = _within(
        build_root, executable.get("path"), "qualified executable path"
    )
    _require_equal(
        _sha256(executable_path),
        record.get("executable_digest"),
        "executable checksum",
    )
    _require_equal(
        _digest(executable.get("sha256")),
        record.get("executable_digest"),
        "executable evidence",
    )
    if executable.get("size_bytes") is not None:
        _require_equal(
            executable_path.stat().st_size,
            executable.get("size_bytes"),
            "executable size",
        )

    expected_outputs: dict[str, str] = {}
    for field, record_key, label, output_key in (
        (image.get("smoke"), "smoke_log_sha256", "image smoke", "smoke"),
        (
            image.get("runtime_probe"),
            "runtime_probe_log_sha256",
            "runtime probe",
            "probe",
        ),
    ):
        if not isinstance(field, Mapping) or field.get("status") != "passed":
            raise TransferError(f"{label} did not pass qualification")
        log = field.get("log")
        if not isinstance(log, Mapping):
            raise TransferError(f"{label} log evidence is absent")
        log_path = _within(build_root, log.get("path"), f"{label} log path")
        _require_equal(
            _sha256(log_path), record.get(record_key), f"{label} log checksum"
        )
        _require_equal(
            _digest(log.get("sha256")),
            record.get(record_key),
            f"{label} log evidence",
        )
        expected_outputs[output_key] = log_path.read_text(encoding="utf-8")
    runtime_probe = image.get("runtime_probe")
    assert isinstance(runtime_probe, Mapping)
    _require_equal(
        runtime_probe.get("image"),
        record.get("runtime_probe_image"),
        "runtime probe image",
    )
    build = result.get("build")
    for artifact, label in (
        (build.get("log") if isinstance(build, Mapping) else None, "compiler build log"),
        (image.get("build_log"), "image build log"),
    ):
        if not isinstance(artifact, Mapping):
            raise TransferError(f"{label} evidence is absent")
        artifact_path = _within(build_root, artifact.get("path"), f"{label} path")
        _require_equal(
            _sha256(artifact_path),
            _digest(artifact.get("sha256")),
            f"{label} checksum",
        )
        if artifact.get("size_bytes") is not None:
            _require_equal(
                artifact_path.stat().st_size,
                artifact.get("size_bytes"),
                f"{label} size",
            )
    return result, executable_path, expected_outputs


def _copy_qualification(build_root: Path, destination: Path) -> None:
    destination.mkdir()
    for dirname in QUALIFICATION_DIRS:
        source = build_root / dirname
        if source.is_dir():
            shutil.copytree(source, destination / dirname)


def _subprocess_runner(
    argv: Sequence[str], *, output_path: Path | None = None
) -> str:
    try:
        result = subprocess.run(
            argv, check=True, text=True, capture_output=True
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise TransferError(
            f"Docker verification command failed: {argv[0]} {argv[1]}"
        ) from error
    return result.stdout


def _inspect_image(
    record: Mapping[str, Any], runner: Runner
) -> dict[str, Any]:
    try:
        raw = runner(
            ["docker", "image", "inspect", str(record["image_reference"])]
        )
        values = json.loads(raw)
    except (KeyError, json.JSONDecodeError, TypeError) as error:
        raise TransferError("cannot inspect transferred target image") from error
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise TransferError("target image inspection is ambiguous")
    image = values[0]
    _require_equal(
        image.get("Id"), record.get("image_digest"), "image config digest"
    )
    if record.get("image_reference") not in (image.get("RepoDigests") or []):
        raise TransferError(
            "immutable image repository digest is not locally resolvable"
        )
    if (image.get("Os"), image.get("Architecture")) != ("linux", "amd64"):
        raise TransferError("measurement target platform must be linux/amd64")
    labels = (image.get("Config") or {}).get("Labels") or {}
    _require_equal(
        labels.get("org.opencontainers.image.revision"),
        record.get("source_revision"),
        "image source label",
    )
    _require_equal(
        labels.get("org.dwarf.measurement.patch-sha256"),
        record.get("patch_set_sha256"),
        "image patch label",
    )
    return image


def _inspect_probe_image(reference: str, runner: Runner) -> None:
    try:
        values = json.loads(
            runner(["docker", "image", "inspect", reference])
        )
    except (json.JSONDecodeError, TypeError) as error:
        raise TransferError("cannot inspect runtime probe image") from error
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise TransferError("runtime probe image inspection is ambiguous")
    image = values[0]
    if reference not in (image.get("RepoDigests") or []):
        raise TransferError("runtime probe image digest is not locally resolvable")
    if (image.get("Os"), image.get("Architecture")) != ("linux", "amd64"):
        raise TransferError("runtime probe image platform must be linux/amd64")


def _read_tar_json(archive: tarfile.TarFile, name: str, label: str) -> dict[str, Any] | list[Any]:
    try:
        member = archive.getmember(name)
        stream = archive.extractfile(member)
        if stream is None or not member.isfile():
            raise TransferError(f"OCI archive {label} is not a regular file")
        value = json.loads(stream.read())
    except (KeyError, json.JSONDecodeError, OSError) as error:
        raise TransferError(f"cannot read OCI archive {label}") from error
    if not isinstance(value, (dict, list)):
        raise TransferError(f"OCI archive {label} has the wrong JSON type")
    return value


def _validate_oci_archive(
    path: Path, record: Mapping[str, Any], *, source_tag: str
) -> dict[str, str]:
    try:
        archive = tarfile.open(path, "r:*")
    except (OSError, tarfile.TarError) as error:
        raise TransferError("cannot open Docker OCI image archive") from error
    with archive:
        names: set[str] = set()
        for member in archive.getmembers():
            rel = _safe_relative(member.name, "OCI archive member")
            if rel.as_posix() in names:
                raise TransferError("OCI archive contains a duplicate member")
            names.add(rel.as_posix())
            if member.issym() or member.islnk():
                raise TransferError("OCI archive contains a link")
        index = _read_tar_json(archive, "index.json", "index")
        legacy = _read_tar_json(archive, "manifest.json", "Docker manifest")
        if not isinstance(index, dict) or not isinstance(legacy, list) or len(legacy) != 1:
            raise TransferError("OCI archive has an ambiguous index or Docker manifest")
        descriptors = index.get("manifests")
        if not isinstance(descriptors, list) or len(descriptors) != 1 or not isinstance(descriptors[0], Mapping):
            raise TransferError("OCI archive must contain exactly one image manifest")
        manifest_digest = str(descriptors[0].get("digest") or "")
        expected_manifest = DIGEST_PREFIX + str(record["image_reference"]).rsplit("@sha256:", 1)[1]
        _require_equal(manifest_digest, expected_manifest, "OCI archive manifest digest")
        manifest_name = "blobs/sha256/" + manifest_digest[7:]
        manifest_member = archive.getmember(manifest_name)
        manifest_bytes = archive.extractfile(manifest_member).read()  # type: ignore[union-attr]
        _require_equal(
            DIGEST_PREFIX + hashlib.sha256(manifest_bytes).hexdigest(),
            manifest_digest,
            "OCI manifest blob checksum",
        )
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("config"), Mapping):
            raise TransferError("OCI image manifest has no config descriptor")
        config_descriptor = manifest["config"]
        config_digest = str(config_descriptor.get("digest") or "")
        if not DIGEST.fullmatch(config_digest):
            raise TransferError("OCI image config descriptor is not immutable")
        config_name = "blobs/sha256/" + config_digest[7:]
        config_member = archive.getmember(config_name)
        config_bytes = archive.extractfile(config_member).read()  # type: ignore[union-attr]
        _require_equal(
            DIGEST_PREFIX + hashlib.sha256(config_bytes).hexdigest(),
            config_digest,
            "OCI config blob checksum",
        )
        config = json.loads(config_bytes)
        if not isinstance(config, dict):
            raise TransferError("OCI image config is not an object")
        if (config.get("os"), config.get("architecture")) != ("linux", "amd64"):
            raise TransferError("OCI image platform must be linux/amd64")
        labels = (config.get("config") or {}).get("Labels") or {}
        _require_equal(
            labels.get("org.opencontainers.image.revision"),
            record.get("source_revision"),
            "OCI config source label",
        )
        _require_equal(
            labels.get("org.dwarf.measurement.patch-sha256"),
            record.get("patch_set_sha256"),
            "OCI config patch label",
        )
        tags = legacy[0].get("RepoTags") if isinstance(legacy[0], Mapping) else None
        if not isinstance(tags, list) or source_tag not in tags:
            raise TransferError("OCI archive does not retain the qualified source tag")
        return {
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
            "platform": "linux/amd64",
            "source_tag": source_tag,
        }


def _inspect_runtime_image(
    config_digest: str, record: Mapping[str, Any], runner: Runner
) -> None:
    try:
        values = json.loads(
            runner(["docker", "image", "inspect", config_digest])
        )
    except (json.JSONDecodeError, TypeError) as error:
        raise TransferError("cannot inspect loaded runtime image") from error
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise TransferError("loaded runtime image inspection is ambiguous")
    image = values[0]
    _require_equal(image.get("Id"), config_digest, "loaded image config digest")
    if (image.get("Os"), image.get("Architecture")) != ("linux", "amd64"):
        raise TransferError("loaded runtime image platform must be linux/amd64")
    labels = (image.get("Config") or {}).get("Labels") or {}
    _require_equal(labels.get("org.opencontainers.image.revision"), record.get("source_revision"), "loaded image source label")
    _require_equal(labels.get("org.dwarf.measurement.patch-sha256"), record.get("patch_set_sha256"), "loaded image patch label")


def _manifest_files(root: Path) -> list[dict[str, Any]]:
    paths = [path for path in root.rglob("*") if path.is_file()]
    entries = []
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        if path == root / "manifest.json":
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return entries


def _conformance_record_path(
    registry_root: str | Path,
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    family: str = "plutus",
) -> Path:
    spec = _conformance_spec(family)
    if implementation not in {"amaru", "cardano-node"}:
        raise TransferError(f"unsupported {spec['label']} conformance implementation")
    if not SHA40.fullmatch(source_revision):
        raise TransferError("adapter source revision is not an exact SHA-40")
    if not SHA64.fullmatch(adapter_set_sha256):
        raise TransferError("adapter set is not an exact SHA-256")
    root = _validated_registry_root(registry_root)
    return (
        root
        / implementation
        / spec["registry_dir"]
        / source_revision
        / f"{adapter_set_sha256}.json"
    )


def _validate_conformance_record(
    record: Mapping[str, Any],
    *,
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    family: str = "plutus",
) -> Path:
    spec = _conformance_spec(family)
    for key, expected in (
        ("schema_version", 1),
        ("kind", spec["record_kind"]),
        ("implementation", implementation),
        ("source_revision", source_revision),
        ("adapter_set_sha256", adapter_set_sha256),
        ("measurement_boundary", spec["measurement_boundary"]),
    ):
        _require_equal(record.get(key), expected, f"adapter {key}")
    for key in ("executable_sha256", "build_result_sha256", "build_log_sha256"):
        if not SHA64.fullmatch(str(record.get(key) or "")):
            raise TransferError(f"adapter {key} is not an exact SHA-256")
    executable = Path(str(record.get("executable") or ""))
    if not executable.is_file() or executable.is_symlink():
        raise TransferError("qualified adapter executable is not a regular file")
    if executable.stat().st_mode & 0o111 == 0:
        raise TransferError("qualified adapter executable is not executable")
    _require_equal(
        _sha256(executable),
        DIGEST_PREFIX + str(record["executable_sha256"]),
        "adapter executable checksum",
    )
    return executable


def _validate_conformance_source(
    package_root: str | Path,
    record: Mapping[str, Any],
    *,
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    family: str = "plutus",
) -> Path:
    spec = _conformance_spec(family)
    package = Path(package_root).resolve(strict=True)
    source_root = (
        package
        / "targets"
        / implementation
        / "conformance-adapters"
        / source_revision
    )
    manifest_path = source_root / spec["manifest_name"]
    manifest = _load_object(manifest_path, f"packaged {spec['label']} qualification manifest")
    source = manifest.get("source")
    for actual, expected, label in (
        (manifest.get("schema_version"), 1, "qualification schema"),
        (manifest.get("kind"), spec["record_kind"], "qualification kind"),
        (manifest.get("implementation"), implementation, "qualification implementation"),
        (
            source.get("revision") if isinstance(source, Mapping) else None,
            source_revision,
            "qualification source revision",
        ),
        (manifest.get("adapter_set_sha256"), adapter_set_sha256, "qualification adapter set"),
        (
            manifest.get("measurement_boundary"),
            spec["measurement_boundary"],
            "qualification measurement boundary",
        ),
        (record.get("adapter_files"), manifest.get("adapter_files"), "qualification adapter files"),
    ):
        _require_equal(actual, expected, label)
    files = manifest.get("adapter_files")
    if not isinstance(files, list) or not files:
        raise TransferError("qualification adapter file inventory is absent")
    for entry in files:
        if not isinstance(entry, Mapping) or not SHA64.fullmatch(
            str(entry.get("sha256") or "")
        ):
            raise TransferError("qualification adapter file entry is invalid")
        path = _within(source_root, entry.get("path"), "qualification adapter source")
        _require_equal(
            _sha256(path),
            DIGEST_PREFIX + str(entry["sha256"]),
            "adapter source digest",
        )
    return manifest_path


def export_conformance_adapter_bundle(
    *,
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    registry_root: str | Path,
    package_root: str | Path,
    source_installation_id: str | None,
    bundle_dir: str | Path,
    legacy_qualified_source: bool = False,
    family: str = "plutus",
) -> dict[str, Any]:
    """Export one already-qualified adapter using the existing bundle contract."""
    if legacy_qualified_source:
        if source_installation_id not in (None, ""):
            raise TransferError(
                "legacy-qualified source must not claim an installation identity"
            )
        source = {"provenance": "legacy-qualified"}
    else:
        if not INSTALLATION_ID.fullmatch(str(source_installation_id or "")):
            raise TransferError("source installation identity is invalid")
        source = {
            "provenance": "installation-owned",
            "installation_id": source_installation_id,
        }
    record_path = _conformance_record_path(
        registry_root, implementation, source_revision, adapter_set_sha256, family
    )
    if record_path.is_symlink():
        raise TransferError("Plutus adapter record must not be a symlink")
    record = _load_object(record_path, "Plutus adapter record")
    transferred_fields = {
        "transfer_manifest",
        "transfer_manifest_sha256",
        "transfer_source_provenance",
        "transfer_source_installation_id",
        "qualification_record",
        "qualification_record_sha256",
        "qualification_manifest",
        "qualification_manifest_sha256",
    }
    if transferred_fields.intersection(record):
        raise TransferError("re-export of an imported adapter is not supported")
    executable = _validate_conformance_record(
        record,
        implementation=implementation,
        source_revision=source_revision,
        adapter_set_sha256=adapter_set_sha256,
        family=family,
    )
    qualification_manifest = _validate_conformance_source(
        package_root,
        record,
        implementation=implementation,
        source_revision=source_revision,
        adapter_set_sha256=adapter_set_sha256,
        family=family,
    )
    bundle = Path(bundle_dir)
    if bundle.exists():
        raise TransferError("bundle destination already exists")
    bundle.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{bundle.name}.", dir=bundle.parent)
    )
    try:
        shutil.copy2(record_path, temporary / "qualification-record.json")
        shutil.copy2(qualification_manifest, temporary / "qualification-manifest.json")
        shutil.copy2(executable, temporary / "adapter-executable")
        (temporary / "adapter-executable").chmod(0o755)
        try:
            artifact_platform = plutus_adapter_executable_platform(executable)
        except MeasurementTargetError as error:
            raise TransferError(str(error)) from error
        identity = {
            "implementation": implementation,
            "source_revision": source_revision,
            "adapter_set_sha256": adapter_set_sha256,
            "executable_sha256": record["executable_sha256"],
            "build_result_sha256": record["build_result_sha256"],
            "platform": artifact_platform,
        }
        manifest = {
            "schema_version": 1,
            "kind": _conformance_spec(family)["bundle_kind"],
            "identity": identity,
            "source": source,
            "qualification_record_sha256": _sha256(
                temporary / "qualification-record.json"
            ),
            "qualification_manifest_sha256": _sha256(
                temporary / "qualification-manifest.json"
            ),
            "files": _manifest_files(temporary),
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, bundle)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "bundle_path": str(bundle),
        "bundle_sha256": _sha256(bundle / "manifest.json"),
        "identity": identity,
    }


def _validate_conformance_bundle(bundle: Path, *, family: str = "plutus") -> dict[str, Any]:
    _validate_tree(bundle)
    manifest = _load_object(bundle / "manifest.json", "adapter transfer manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != _conformance_spec(family)["bundle_kind"]
    ):
        raise TransferError("adapter transfer manifest schema or kind is unsupported")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise TransferError("adapter transfer file inventory is absent")
    expected = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TransferError("adapter transfer file entry is invalid")
        relative = _safe_relative(entry.get("path"), "adapter transfer path")
        path = _within(bundle, relative.as_posix(), "adapter transfer path")
        expected.append(relative.as_posix())
        _require_equal(_sha256(path), entry.get("sha256"), f"checksum for {relative}")
        _require_equal(path.stat().st_size, entry.get("size_bytes"), f"size for {relative}")
    if len(set(expected)) != len(expected) or expected != [
        item["path"] for item in _manifest_files(bundle)
    ]:
        raise TransferError("adapter transfer manifest does not exactly cover bundle files")
    return manifest


def _publish_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    if path.is_symlink():
        raise TransferError("existing adapter artifact must not be a symlink")
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise TransferError("existing adapter artifact differs; refusing to overwrite it")
        if mode & 0o111 and path.stat().st_mode & 0o111 == 0:
            path.chmod(mode)
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _ensure_registry_child_directory(root: Path, parts: Sequence[str]) -> Path:
    current = root
    for part in parts:
        if not part or part in {".", ".."} or "/" in part:
            raise TransferError("adapter registry directory component is invalid")
        current = current / part
        if current.is_symlink():
            raise TransferError("adapter registry directory must not be a symlink")
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if not current.is_dir() or current.is_symlink():
            raise TransferError("adapter registry directory is not an owned directory")
    return current


def _snapshot_conformance_bundle(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise TransferError("adapter transfer bundle is not a regular directory")
    expected = {
        "manifest.json",
        "qualification-record.json",
        "qualification-manifest.json",
        "adapter-executable",
    }
    try:
        names = {entry.name for entry in os.scandir(source)}
    except OSError as error:
        raise TransferError("cannot inspect adapter transfer bundle") from error
    if names != expected:
        raise TransferError("adapter transfer bundle does not have the exact file set")
    destination.mkdir(mode=0o700)
    for name in sorted(expected):
        source_path = source / name
        try:
            descriptor = os.open(
                source_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as error:
            raise TransferError("adapter transfer bundle contains an unsafe file") from error
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise TransferError("adapter transfer bundle contains a non-regular file")
            chunks = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise TransferError("adapter transfer bundle changed while being snapshotted")
        finally:
            os.close(descriptor)
        snapshot = destination / name
        snapshot.write_bytes(b"".join(chunks))
        snapshot.chmod(0o700 if name == "adapter-executable" else 0o600)


def _import_conformance_adapter_snapshot(
    *,
    bundle_dir: str | Path,
    registry_root: str | Path,
    package_root: str | Path,
    family: str = "plutus",
) -> dict[str, Any]:
    """Verify and publish one transferred adapter under the installation registry."""
    bundle = Path(bundle_dir)
    manifest = (
        _validate_conformance_bundle(bundle)
        if family == "plutus"
        else _validate_conformance_bundle(bundle, family=family)
    )
    identity = manifest.get("identity")
    source = manifest.get("source")
    if not isinstance(identity, Mapping) or not isinstance(source, Mapping):
        raise TransferError("adapter transfer identity or source is absent")
    source_installation = str(source.get("installation_id") or "")
    source_provenance = str(source.get("provenance") or "")
    if source_installation:
        if not INSTALLATION_ID.fullmatch(source_installation):
            raise TransferError("adapter transfer source installation is invalid")
        if source_provenance not in ("", "installation-owned"):
            raise TransferError("adapter transfer source provenance is inconsistent")
        expected_source_keys = (
            {"installation_id"}
            if not source_provenance
            else {"provenance", "installation_id"}
        )
        if set(source) != expected_source_keys:
            raise TransferError("adapter transfer source provenance is malformed")
        source_provenance = "installation-owned"
    elif source_provenance == "legacy-qualified":
        if set(source) != {"provenance"}:
            raise TransferError("legacy-qualified adapter source is malformed")
    else:
        raise TransferError("adapter transfer source provenance is invalid")
    implementation = str(identity.get("implementation") or "")
    source_revision = str(identity.get("source_revision") or "")
    adapter_set = str(identity.get("adapter_set_sha256") or "")
    record_path = bundle / "qualification-record.json"
    executable_path = bundle / "adapter-executable"
    qualification_manifest = bundle / "qualification-manifest.json"
    _require_equal(
        _sha256(record_path),
        manifest.get("qualification_record_sha256"),
        "qualification record checksum",
    )
    _require_equal(
        _sha256(qualification_manifest),
        manifest.get("qualification_manifest_sha256"),
        "qualification manifest checksum",
    )
    record = _load_object(record_path, "qualification record")
    _validate_conformance_record(
        {**record, "executable": str(executable_path)},
        implementation=implementation,
        source_revision=source_revision,
        adapter_set_sha256=adapter_set,
        family=family,
    )
    packaged_manifest = _validate_conformance_source(
        package_root,
        record,
        implementation=implementation,
        source_revision=source_revision,
        adapter_set_sha256=adapter_set,
        family=family,
    )
    if packaged_manifest.read_bytes() != qualification_manifest.read_bytes():
        raise TransferError(
            "transferred qualification manifest differs from the packaged manifest"
        )
    _require_equal(
        _sha256(executable_path),
        DIGEST_PREFIX + str(identity.get("executable_sha256") or ""),
        "transferred adapter executable",
    )
    _require_equal(
        record.get("build_result_sha256"),
        identity.get("build_result_sha256"),
        "transferred adapter build result",
    )
    try:
        artifact_platform = plutus_adapter_executable_platform(executable_path)
    except MeasurementTargetError as error:
        raise TransferError(str(error)) from error
    _require_equal(
        artifact_platform,
        identity.get("platform"),
        "transferred adapter platform",
    )

    root = _validated_registry_root(registry_root)
    lock_fd = os.open(
        root / ".transfer.lock",
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        parent = _ensure_registry_child_directory(
            root, (implementation, _conformance_spec(family)["registry_dir"], source_revision)
        )
        destination = parent / f"{adapter_set}.json"
        executable_destination = destination.with_suffix(".bin")
        qualification_destination = destination.with_suffix(".qualification.json")
        qualification_manifest_destination = destination.with_suffix(
            ".qualification-manifest.json"
        )
        transfer_destination = destination.with_suffix(".transfer.json")
        published = {
            **record,
            "executable": executable_destination.relative_to(root).as_posix(),
            "qualification_record": qualification_destination.relative_to(root).as_posix(),
            "qualification_record_sha256": manifest["qualification_record_sha256"],
            "qualification_manifest": qualification_manifest_destination.relative_to(root).as_posix(),
            "qualification_manifest_sha256": manifest["qualification_manifest_sha256"],
            "transfer_manifest": transfer_destination.relative_to(root).as_posix(),
            "transfer_manifest_sha256": _sha256(bundle / "manifest.json"),
            "transfer_source_provenance": source_provenance,
            "platform": artifact_platform,
        }
        if source_installation:
            published["transfer_source_installation_id"] = source_installation
        published_bytes = (
            json.dumps(published, indent=2, sort_keys=True) + "\n"
        ).encode()
        _publish_bytes(executable_destination, executable_path.read_bytes(), mode=0o755)
        _publish_bytes(qualification_destination, record_path.read_bytes(), mode=0o600)
        _publish_bytes(
            qualification_manifest_destination,
            qualification_manifest.read_bytes(),
            mode=0o600,
        )
        _publish_bytes(transfer_destination, (bundle / "manifest.json").read_bytes(), mode=0o600)
        _publish_bytes(destination, published_bytes, mode=0o600)
    return {"record_path": str(destination), "identity": dict(identity)}


def import_conformance_adapter_bundle(
    *,
    bundle_dir: str | Path,
    registry_root: str | Path,
    package_root: str | Path,
    family: str = "plutus",
) -> dict[str, Any]:
    """Snapshot, verify, and atomically publish a transferred adapter."""
    root = _validated_registry_root(registry_root)
    with tempfile.TemporaryDirectory(
        prefix=".adapter-transfer-snapshot-", dir=root
    ) as temporary:
        snapshot = Path(temporary) / "bundle"
        _snapshot_conformance_bundle(Path(bundle_dir), snapshot)
        return _import_conformance_adapter_snapshot(
            bundle_dir=snapshot,
            registry_root=root,
            package_root=package_root,
            family=family,
        )


def export_bundle(
    registry_root: str | Path,
    build_root: str | Path,
    bundle_dir: str | Path,
    *,
    source_revision: str,
    patch_set_sha256: str,
    source_installation_id: str,
    runner: Runner = _subprocess_runner,
) -> dict[str, Any]:
    if not INSTALLATION_ID.fullmatch(source_installation_id):
        raise TransferError("source installation identity is invalid")
    record_path = _record_path(
        registry_root, source_revision, patch_set_sha256
    )
    build_root = Path(build_root)
    bundle_dir = Path(bundle_dir)
    if bundle_dir.exists():
        raise TransferError("bundle destination already exists")
    record = _load_object(record_path, "target record")
    _require_equal(
        record.get("source_revision"),
        source_revision,
        "source registry revision",
    )
    _require_equal(
        record.get("patch_set_sha256"),
        patch_set_sha256,
        "source registry patch set",
    )
    result, _, _ = _validate_record_and_result(record, build_root)
    inspected = _inspect_image(record, runner)
    image = result.get("image")
    source_tag = str(image.get("reference") or "") if isinstance(image, Mapping) else ""
    if not source_tag or "@" in source_tag or source_tag not in (inspected.get("RepoTags") or []):
        raise TransferError("qualified source image tag is absent or ambiguous")
    bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{bundle_dir.name}.", dir=bundle_dir.parent
        )
    )
    try:
        _copy_qualification(build_root, temporary / "qualification")
        shutil.copy2(record_path, temporary / "target-record.json")
        archive = temporary / "image.tar"
        runner(
            [
                "docker",
                "image",
                "save",
                "--output",
                str(archive),
                source_tag,
            ],
            output_path=archive,
        )
        if not archive.is_file() or archive.is_symlink():
            raise TransferError("Docker image archive was not created")
        archive_identity = _validate_oci_archive(
            archive, record, source_tag=source_tag
        )
        manifest = {
            "schema_version": 1,
            "kind": BUNDLE_KIND,
            "identity": {
                key: record[key]
                for key in (
                    "implementation",
                    "version",
                    "source_revision",
                    "patch_set_sha256",
                    "image_reference",
                    "image_digest",
                )
            },
            "source": {
                "installation_id": source_installation_id,
                "record": record_path.relative_to(
                    _validated_registry_root(registry_root)
                ).as_posix(),
            },
            "archive": archive_identity,
            "files": _manifest_files(temporary),
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, bundle_dir)
        return {
            "bundle_path": str(bundle_dir),
            "bundle_sha256": _sha256(bundle_dir / "manifest.json"),
            "identity": manifest["identity"],
        }
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _validate_manifest(bundle: Path) -> dict[str, Any]:
    _validate_tree(bundle)
    manifest = _load_object(bundle / "manifest.json", "transfer manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != BUNDLE_KIND
    ):
        raise TransferError("transfer manifest schema or kind is unsupported")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise TransferError("transfer manifest file inventory is absent")
    expected_paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TransferError("transfer manifest file entry is invalid")
        rel = _safe_relative(entry.get("path"), "manifest file path")
        path = _within(bundle, rel.as_posix(), "manifest file path")
        expected_paths.append(rel.as_posix())
        _require_equal(
            _sha256(path), entry.get("sha256"), f"checksum for {rel}"
        )
        _require_equal(
            path.stat().st_size, entry.get("size_bytes"), f"size for {rel}"
        )
    if len(set(expected_paths)) != len(expected_paths):
        raise TransferError("transfer manifest contains duplicate paths")
    actual_paths = [entry["path"] for entry in _manifest_files(bundle)]
    if expected_paths != actual_paths:
        raise TransferError(
            "transfer manifest does not exactly cover bundle files"
        )
    return manifest


def import_bundle(
    bundle_dir: str | Path,
    registry_root: str | Path,
    *,
    runner: Runner = _subprocess_runner,
) -> dict[str, Any]:
    bundle = Path(bundle_dir)
    manifest = _validate_manifest(bundle)
    record = _load_object(bundle / "target-record.json", "target record")
    _, executable, expected_outputs = _validate_record_and_result(
        record, bundle / "qualification"
    )
    expected_identity = manifest.get("identity")
    actual_identity = {
        key: record[key]
        for key in (
            "implementation",
            "version",
            "source_revision",
            "patch_set_sha256",
            "image_reference",
            "image_digest",
        )
    }
    _require_equal(expected_identity, actual_identity, "bundle identity")
    source = manifest.get("source")
    if not isinstance(source, Mapping) or not INSTALLATION_ID.fullmatch(
        str(source.get("installation_id") or "")
    ):
        raise TransferError("bundle source installation identity is absent")
    _require_equal(
        source.get("record"),
        f"amaru/{record['source_revision']}/{record['patch_set_sha256']}.json",
        "bundle source record",
    )
    archive_identity = manifest.get("archive")
    if not isinstance(archive_identity, Mapping):
        raise TransferError("bundle OCI archive identity is absent")
    verified_archive = _validate_oci_archive(
        bundle / "image.tar",
        record,
        source_tag=str(archive_identity.get("source_tag") or ""),
    )
    _require_equal(archive_identity, verified_archive, "bundle OCI archive identity")

    runner(["docker", "load", "--input", str(bundle / "image.tar")])
    config_digest = verified_archive["config_digest"]
    _inspect_runtime_image(config_digest, record, runner)
    _inspect_probe_image(str(record["runtime_probe_image"]), runner)
    embedded_digest = runner(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "sha256sum",
            config_digest,
            "/usr/local/bin/amaru",
        ]
    ).split()
    if not embedded_digest or not SHA64.fullmatch(embedded_digest[0]):
        raise TransferError("transferred target executable digest output is invalid")
    _require_equal(
        DIGEST_PREFIX + embedded_digest[0],
        record.get("executable_digest"),
        "transferred target executable digest",
    )
    smoke_output = runner(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "/usr/local/bin/amaru",
            config_digest,
            "--version",
        ]
    )
    _require_equal(
        smoke_output, expected_outputs["smoke"], "transferred target smoke output"
    )
    probe_output = runner(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--volume",
            f"{executable.resolve()}:/target/amaru:ro",
            "--entrypoint",
            "/bin/bash",
            str(record["runtime_probe_image"]),
            "-lc",
            "/target/amaru --version",
        ]
    )
    _require_equal(
        probe_output, expected_outputs["probe"], "transferred runtime probe output"
    )

    published_record = dict(record)
    published_record.update(
        {
            "qualification_image_id": record["image_digest"],
            "image_manifest_digest": verified_archive["manifest_digest"],
            "image_config_digest": config_digest,
            "runtime_image_reference": config_digest,
            "image_digest": config_digest,
            "transfer_source_installation_id": source["installation_id"],
            "transfer_manifest_sha256": _sha256(bundle / "manifest.json"),
        }
    )

    destination = _record_path(
        registry_root,
        str(record["source_revision"]),
        str(record["patch_set_sha256"]),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _validated_registry_root(registry_root) / ".transfer.lock"
    lock_fd = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        destination = _record_path(
            registry_root,
            str(record["source_revision"]),
            str(record["patch_set_sha256"]),
        )
        if destination.is_symlink():
            raise TransferError("existing target record must not be a symlink")
        if destination.exists():
            existing = _load_object(destination, "existing target record")
            if existing != published_record:
                raise TransferError(
                    "existing target record differs; refusing to overwrite it"
                )
        else:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(published_record, stream, indent=2, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, destination)
            except BaseException:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
    return {
        "record_path": str(destination),
        "identity": {
            **actual_identity,
            "qualification_image_id": record["image_digest"],
            "manifest_digest": verified_archive["manifest_digest"],
            "config_digest": config_digest,
            "runtime_image_reference": config_digest,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--registry-root", type=Path, required=True)
    export.add_argument("--source-revision", required=True)
    export.add_argument("--patch-set-sha256", required=True)
    export.add_argument("--source-installation-id", required=True)
    export.add_argument("--build-root", type=Path, required=True)
    export.add_argument("--bundle", type=Path, required=True)
    imported = subparsers.add_parser("import")
    imported.add_argument("--bundle", type=Path, required=True)
    imported.add_argument("--registry-root", type=Path, required=True)
    conformance_export = subparsers.add_parser("export-conformance")
    conformance_export.add_argument("--registry-root", type=Path, required=True)
    conformance_export.add_argument("--implementation", required=True)
    conformance_export.add_argument("--source-revision", required=True)
    conformance_export.add_argument("--adapter-set-sha256", required=True)
    conformance_export.add_argument("--source-installation-id")
    conformance_export.add_argument("--legacy-qualified-source", action="store_true")
    conformance_export.add_argument("--package-root", type=Path, required=True)
    conformance_export.add_argument("--bundle", type=Path, required=True)
    conformance_import = subparsers.add_parser("import-conformance")
    conformance_import.add_argument("--bundle", type=Path, required=True)
    conformance_import.add_argument("--registry-root", type=Path, required=True)
    conformance_import.add_argument("--package-root", type=Path, required=True)
    cbor_export = subparsers.add_parser("export-cbor-conformance")
    cbor_export.add_argument("--registry-root", type=Path, required=True)
    cbor_export.add_argument("--implementation", required=True)
    cbor_export.add_argument("--source-revision", required=True)
    cbor_export.add_argument("--adapter-set-sha256", required=True)
    cbor_export.add_argument("--source-installation-id")
    cbor_export.add_argument("--legacy-qualified-source", action="store_true")
    cbor_export.add_argument("--package-root", type=Path, required=True)
    cbor_export.add_argument("--bundle", type=Path, required=True)
    cbor_import = subparsers.add_parser("import-cbor-conformance")
    cbor_import.add_argument("--bundle", type=Path, required=True)
    cbor_import.add_argument("--registry-root", type=Path, required=True)
    cbor_import.add_argument("--package-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "export":
            result = export_bundle(
                args.registry_root,
                args.build_root,
                args.bundle,
                source_revision=args.source_revision,
                patch_set_sha256=args.patch_set_sha256,
                source_installation_id=args.source_installation_id,
            )
        elif args.action == "import":
            result = import_bundle(
                args.bundle, args.registry_root
            )
        elif args.action in {"export-conformance", "export-cbor-conformance"}:
            result = export_conformance_adapter_bundle(
                implementation=args.implementation,
                source_revision=args.source_revision,
                adapter_set_sha256=args.adapter_set_sha256,
                registry_root=args.registry_root,
                package_root=args.package_root,
                source_installation_id=args.source_installation_id,
                legacy_qualified_source=args.legacy_qualified_source,
                bundle_dir=args.bundle,
                family=("cbor" if args.action == "export-cbor-conformance" else "plutus"),
            )
        elif args.action in {"import-conformance", "import-cbor-conformance"}:
            result = import_conformance_adapter_bundle(
                bundle_dir=args.bundle,
                registry_root=args.registry_root,
                package_root=args.package_root,
                family=("cbor" if args.action == "import-cbor-conformance" else "plutus"),
            )
        else:
            result = import_bundle(args.bundle, args.registry_root)
    except TransferError as error:
        print(
            f"measurement-target transfer failed: {error}",
            file=os.sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
