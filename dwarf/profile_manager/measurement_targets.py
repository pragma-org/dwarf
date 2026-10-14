"""Fail-closed resolution of locally built, revision-locked measurement targets."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from pathlib import Path, PurePosixPath
from typing import Any

from profile_manager.config import storage_roots


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TRANSFER_SOURCE_INSTALLATION_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
PLUTUS_CONFORMANCE_PROVENANCE_FIELDS = (
    "implementation",
    "source_revision",
    "adapter_set_sha256",
    "executable_sha256",
    "build_result_sha256",
    "build_log_sha256",
    "qualification_record_sha256",
    "qualification_manifest_sha256",
    "transfer_manifest_sha256",
    "transfer_source_provenance",
    "transfer_source_installation_id",
    "platform",
)


class MeasurementTargetError(ValueError):
    """A requested instrumented target is absent or does not match its audit identity."""


def plutus_conformance_adapter_provenance(record: dict[str, Any]) -> dict[str, Any]:
    """Return the complete immutable provenance carried by a resolved adapter."""
    return {
        field: record[field]
        for field in PLUTUS_CONFORMANCE_PROVENANCE_FIELDS
        if record.get(field) not in (None, "")
    }


def plutus_adapter_executable_platform(path: str | Path) -> str:
    """Identify the supported Linux ELF architecture without executing it."""
    try:
        with Path(path).open("rb") as stream:
            header = stream.read(20)
    except OSError as error:
        raise MeasurementTargetError("cannot read Plutus adapter executable header") from error
    if len(header) < 20 or header[:4] != b"\x7fELF":
        raise MeasurementTargetError("Plutus adapter executable is not an ELF binary")
    byteorder = {1: "little", 2: "big"}.get(header[5])
    if byteorder is None:
        raise MeasurementTargetError("Plutus adapter ELF byte order is unsupported")
    machine = int.from_bytes(header[18:20], byteorder)
    architecture = {0x3E: "amd64", 0xB7: "arm64"}.get(machine)
    if architecture is None:
        raise MeasurementTargetError("Plutus adapter ELF architecture is unsupported")
    return f"linux/{architecture}"


def _host_platform(host_os: str | None, host_arch: str | None) -> str:
    operating_system = str(host_os or platform.system()).strip().casefold()
    architecture = str(host_arch or platform.machine()).strip().casefold()
    architecture = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(architecture, architecture)
    return f"{operating_system}/{architecture}"


def _registry_relative_path(root: Path, value: Any, label: str) -> Path:
    text = str(value or "")
    relative = PurePosixPath(text)
    if (
        not text
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != text
    ):
        raise MeasurementTargetError(
            f"imported Plutus adapter {label} is not registry-relative"
        )
    candidate = root.joinpath(*relative.parts)
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise MeasurementTargetError(
            f"imported Plutus adapter {label} escapes the registry"
        ) from error
    return candidate


def measurement_target_registry_root() -> Path:
    explicit = os.environ.get("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", "").strip()
    if explicit:
        return Path(explicit)
    return storage_roots().state / "measurement-targets"


def measurement_target_record_path(
    implementation: str,
    source_revision: str,
    patch_set_sha256: str,
    *,
    registry_root: str | Path | None = None,
) -> Path:
    root = Path(registry_root) if registry_root is not None else measurement_target_registry_root()
    return root / implementation / source_revision / f"{patch_set_sha256}.json"


def coverage_target_record_path(
    implementation: str,
    source_revision: str,
    coverage_harness_sha256: str,
    *,
    registry_root: str | Path | None = None,
) -> Path:
    root = Path(registry_root) if registry_root is not None else measurement_target_registry_root()
    return (
        root
        / implementation
        / "coverage"
        / source_revision
        / f"{coverage_harness_sha256}.json"
    )


def plutus_conformance_adapter_record_path(
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    *,
    registry_root: str | Path | None = None,
) -> Path:
    root = Path(registry_root) if registry_root is not None else measurement_target_registry_root()
    return (
        root
        / implementation
        / "plutus-conformance"
        / source_revision
        / f"{adapter_set_sha256}.json"
    )


def cbor_conformance_adapter_record_path(
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    *,
    registry_root: str | Path | None = None,
) -> Path:
    root = Path(registry_root) if registry_root is not None else measurement_target_registry_root()
    return (
        root
        / implementation
        / "conformance"
        / source_revision
        / f"{adapter_set_sha256}.json"
    )


def _resolve_conformance_adapter(
    *,
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    registry_root: str | Path | None = None,
    host_os: str | None = None,
    host_arch: str | None = None,
    family: str,
    record_kind: str,
    measurement_boundary: str,
    transfer_kind: str,
    label: str,
) -> dict[str, Any]:
    """Resolve one exact qualified binary adapter and its retained provenance."""
    if implementation not in {"amaru", "cardano-node"}:
        raise MeasurementTargetError(f"unsupported {label} conformance implementation")
    if not SHA40.fullmatch(source_revision):
        raise MeasurementTargetError(f"{label} adapter source revision is not exact")
    if not SHA64.fullmatch(adapter_set_sha256):
        raise MeasurementTargetError(f"{label} adapter set digest is not immutable sha256")
    root = Path(registry_root) if registry_root is not None else measurement_target_registry_root()
    path = (
        root
        / implementation
        / family
        / source_revision
        / f"{adapter_set_sha256}.json"
    )
    if not path.is_file() or path.is_symlink():
        raise MeasurementTargetError(
            f"qualified {label} conformance adapter is unavailable for {implementation} "
            f"source {source_revision} and adapter set {adapter_set_sha256}"
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MeasurementTargetError(f"cannot read {label} adapter record {path}: {error}") from error
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise MeasurementTargetError(f"{label} adapter record is not schema version 1")
    for key, expected in (
        ("kind", record_kind),
        ("implementation", implementation),
        ("source_revision", source_revision),
        ("adapter_set_sha256", adapter_set_sha256),
        ("measurement_boundary", measurement_boundary),
    ):
        if record.get(key) != expected:
            raise MeasurementTargetError(f"{label} adapter {key} mismatch")
    for key in ("executable_sha256", "build_result_sha256", "build_log_sha256"):
        if not SHA64.fullmatch(str(record.get(key) or "")):
            raise MeasurementTargetError(f"{label} adapter {key} is not immutable sha256")
    transfer_fields = (
        "transfer_manifest",
        "transfer_manifest_sha256",
        "transfer_source_provenance",
        "transfer_source_installation_id",
        "qualification_record",
        "qualification_record_sha256",
        "qualification_manifest",
        "qualification_manifest_sha256",
    )
    imported = any(record.get(field) is not None for field in transfer_fields)
    executable = (
        _registry_relative_path(root, record.get("executable"), "executable")
        if imported
        else Path(str(record.get("executable") or ""))
    )
    if not executable.is_file() or executable.is_symlink():
        raise MeasurementTargetError(f"{label} adapter executable is unavailable")
    if executable.stat().st_mode & 0o111 == 0:
        raise MeasurementTargetError(f"{label} adapter executable is not executable")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    if digest != record["executable_sha256"]:
        raise MeasurementTargetError(f"{label} adapter executable digest mismatch")
    artifact_platform = plutus_adapter_executable_platform(executable)
    if record.get("platform") not in (None, artifact_platform):
        raise MeasurementTargetError(f"{label} adapter platform identity mismatch")
    if artifact_platform != _host_platform(host_os, host_arch):
        raise MeasurementTargetError(
            f"qualified {label} conformance adapter requires {artifact_platform}, "
            f"not {_host_platform(host_os, host_arch)}"
        )
    if imported:
        for field in (
            "transfer_manifest_sha256",
            "qualification_record_sha256",
            "qualification_manifest_sha256",
        ):
            if not DIGEST.fullmatch(str(record.get(field) or "")):
                raise MeasurementTargetError(
                    f"{label} adapter {field.replace('_', ' ')} is not immutable"
                )
        source_provenance = str(record.get("transfer_source_provenance") or "")
        source_installation = str(record.get("transfer_source_installation_id") or "")
        if source_provenance in ("", "installation-owned"):
            if not TRANSFER_SOURCE_INSTALLATION_ID.fullmatch(source_installation):
                raise MeasurementTargetError(f"{label} adapter transfer source is invalid")
            source_provenance = "installation-owned"
        elif source_provenance == "legacy-qualified":
            if source_installation:
                raise MeasurementTargetError(
                    f"legacy-qualified {label} adapter must not claim a source installation"
                )
        else:
            raise MeasurementTargetError(f"{label} adapter transfer source is invalid")
        retained = {
            "executable": executable,
            "qualification record": _registry_relative_path(
                root, record.get("qualification_record"), "qualification record"
            ),
            "qualification manifest": _registry_relative_path(
                root, record.get("qualification_manifest"), "qualification manifest"
            ),
            "transfer manifest": _registry_relative_path(
                root, record.get("transfer_manifest"), "transfer manifest"
            ),
        }
        for retained_label, retained_path in retained.items():
            if not retained_path.is_file() or retained_path.is_symlink():
                raise MeasurementTargetError(
                    f"imported {label} adapter {retained_label} is unavailable"
                )
        for retained_label, field in (
            ("qualification record", "qualification_record_sha256"),
            ("qualification manifest", "qualification_manifest_sha256"),
            ("transfer manifest", "transfer_manifest_sha256"),
        ):
            retained_path = retained[retained_label]
            retained_digest = "sha256:" + hashlib.sha256(retained_path.read_bytes()).hexdigest()
            if retained_digest != record[field]:
                raise MeasurementTargetError(
                    f"imported {label} adapter {retained_label} digest mismatch"
                )
        try:
            original = json.loads(retained["qualification record"].read_text(encoding="utf-8"))
            transfer = json.loads(retained["transfer manifest"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MeasurementTargetError(
                f"imported {label} adapter provenance is unreadable"
            ) from error
        if not isinstance(original, dict) or not isinstance(transfer, dict):
            raise MeasurementTargetError(f"imported {label} adapter provenance is invalid")
        for field in (
            "implementation",
            "source_revision",
            "adapter_set_sha256",
            "executable_sha256",
            "build_result_sha256",
            "build_log_sha256",
        ):
            if original.get(field) != record.get(field):
                raise MeasurementTargetError(
                    f"imported {label} adapter original {field} mismatch"
                )
        transfer_identity = transfer.get("identity") or {}
        transfer_source = transfer.get("source") or {}
        if source_provenance == "installation-owned":
            expected_transfer_source = (
                {"installation_id": source_installation}
                if "provenance" not in transfer_source
                else {
                    "provenance": "installation-owned",
                    "installation_id": source_installation,
                }
            )
        else:
            expected_transfer_source = {"provenance": "legacy-qualified"}
        if (
            transfer.get("kind") != transfer_kind
            or transfer_identity.get("implementation") != implementation
            or transfer_identity.get("source_revision") != source_revision
            or transfer_identity.get("adapter_set_sha256") != adapter_set_sha256
            or transfer_source != expected_transfer_source
        ):
            raise MeasurementTargetError(f"imported {label} adapter transfer identity mismatch")
    resolved = dict(record)
    resolved["executable"] = str(executable)
    resolved["platform"] = artifact_platform
    resolved["record_path"] = str(path)
    return resolved


def resolve_plutus_conformance_adapter(
    *,
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    registry_root: str | Path | None = None,
    host_os: str | None = None,
    host_arch: str | None = None,
) -> dict[str, Any]:
    return _resolve_conformance_adapter(
        implementation=implementation,
        source_revision=source_revision,
        adapter_set_sha256=adapter_set_sha256,
        registry_root=registry_root,
        host_os=host_os,
        host_arch=host_arch,
        family="plutus-conformance",
        record_kind="production-plutus-v2-conformance",
        measurement_boundary="production-plutus-v2-vm-only",
        transfer_kind="dwarf-plutus-conformance-adapter-transfer",
        label="Plutus",
    )


def resolve_cbor_conformance_adapter(
    *,
    implementation: str,
    source_revision: str,
    adapter_set_sha256: str,
    registry_root: str | Path | None = None,
    host_os: str | None = None,
    host_arch: str | None = None,
) -> dict[str, Any]:
    return _resolve_conformance_adapter(
        implementation=implementation,
        source_revision=source_revision,
        adapter_set_sha256=adapter_set_sha256,
        registry_root=registry_root,
        host_os=host_os,
        host_arch=host_arch,
        family="conformance",
        record_kind="production-cbor-conformance",
        measurement_boundary="production-codec-only",
        transfer_kind="dwarf-cbor-conformance-adapter-transfer",
        label="CBOR",
    )


def cbor_conformance_adapter_provenance(record: dict[str, Any]) -> dict[str, Any]:
    return plutus_conformance_adapter_provenance(record)


def _require(record: dict[str, Any], key: str, expected: Any) -> None:
    actual = record.get(key)
    if actual != expected:
        label = key.replace("_", " ")
        raise MeasurementTargetError(
            f"patched target {label} mismatch: expected {expected}, got {actual}"
        )


def resolve_patched_amaru_target(profile, *, registry_root: str | Path | None = None) -> dict[str, Any]:
    revision = str(profile.measurement_patch_revision or "")
    patch_set = str(profile.measurement_patch_set_sha256 or "")
    if not SHA40.fullmatch(revision):
        raise MeasurementTargetError("patched Amaru profile requires an exact source revision")
    if not SHA64.fullmatch(patch_set):
        raise MeasurementTargetError("patched Amaru profile requires an exact patch-set sha256")
    path = measurement_target_record_path(
        "amaru", revision, patch_set, registry_root=registry_root
    )
    if not path.is_file():
        raise MeasurementTargetError(
            f"patched Amaru target is not built for source {revision} and patch set {patch_set}"
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MeasurementTargetError(f"cannot read patched target record {path}: {error}") from error
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise MeasurementTargetError("patched target record is not schema version 1")
    _require(record, "implementation", "amaru")
    _require(record, "mode", "patched")
    _require(record, "version", profile.amaru_version)
    _require(record, "source_revision", revision)
    _require(record, "patch_set_sha256", patch_set)
    if (
        not isinstance(record.get("image_reference"), str)
        or "@sha256:" not in record["image_reference"]
    ):
        raise MeasurementTargetError("patched target image reference is not immutable sha256")
    if not DIGEST.fullmatch(str(record.get("image_digest") or "")):
        raise MeasurementTargetError("patched target image digest is not immutable sha256")
    if not DIGEST.fullmatch(str(record.get("executable_digest") or "")):
        raise MeasurementTargetError("patched target executable digest is not immutable sha256")
    if not DIGEST.fullmatch(str(record.get("build_result_sha256") or "")):
        raise MeasurementTargetError("patched target build-result digest is not immutable sha256")
    if (
        not isinstance(record.get("runtime_probe_image"), str)
        or "@sha256:" not in record["runtime_probe_image"]
    ):
        raise MeasurementTargetError("patched target runtime probe image is not immutable sha256")
    if not DIGEST.fullmatch(str(record.get("runtime_probe_log_sha256") or "")):
        raise MeasurementTargetError("patched target runtime probe evidence is missing")
    runtime_reference = record.get("runtime_image_reference")
    if runtime_reference is not None:
        if not DIGEST.fullmatch(str(runtime_reference)):
            raise MeasurementTargetError(
                "imported patched target runtime image reference is not a config digest"
            )
        for key in (
            "image_manifest_digest",
            "image_config_digest",
            "qualification_image_id",
            "transfer_manifest_sha256",
        ):
            if not DIGEST.fullmatch(str(record.get(key) or "")):
                raise MeasurementTargetError(
                    f"imported patched target {key} is not immutable sha256"
                )
        manifest_digest = "sha256:" + record["image_reference"].rsplit(
            "@sha256:", 1
        )[1]
        _require(record, "image_manifest_digest", manifest_digest)
        _require(record, "image_config_digest", runtime_reference)
        _require(record, "image_digest", runtime_reference)
        if not TRANSFER_SOURCE_INSTALLATION_ID.fullmatch(
            str(record.get("transfer_source_installation_id") or "")
        ):
            raise MeasurementTargetError(
                "imported patched target source installation identity is invalid"
            )
    return record


def resolve_patched_cardano_target(
    profile, *, registry_root: str | Path | None = None
) -> dict[str, Any]:
    revision = str(profile.measurement_patch_revision or "")
    patch_set = str(profile.measurement_patch_set_sha256 or "")
    if not SHA40.fullmatch(revision):
        raise MeasurementTargetError(
            "patched Cardano profile requires an exact source revision"
        )
    if not SHA64.fullmatch(patch_set):
        raise MeasurementTargetError(
            "patched Cardano profile requires an exact patch-set sha256"
        )
    path = measurement_target_record_path(
        "cardano-node", revision, patch_set, registry_root=registry_root
    )
    if not path.is_file():
        raise MeasurementTargetError(
            f"patched Cardano target is not built for source {revision} "
            f"and patch set {patch_set}"
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MeasurementTargetError(
            f"cannot read patched target record {path}: {error}"
        ) from error
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise MeasurementTargetError("patched target record is not schema version 1")
    for key, expected in (
        ("implementation", "cardano-node"),
        ("mode", "patched"),
        ("version", profile.cardano_version),
        ("source_revision", revision),
        ("patch_set_sha256", patch_set),
    ):
        _require(record, key, expected)
    if (
        not isinstance(record.get("image_reference"), str)
        or "@sha256:" not in record["image_reference"]
    ):
        raise MeasurementTargetError(
            "patched target image reference is not immutable sha256"
        )
    for key in (
        "image_digest", "executable_digest", "build_result_sha256",
        "runtime_probe_log_sha256",
    ):
        if not DIGEST.fullmatch(str(record.get(key) or "")):
            label = "runtime probe evidence" if key == "runtime_probe_log_sha256" else key
            raise MeasurementTargetError(
                f"patched target {label} is missing or not immutable sha256"
            )
    return record


def resolve_coverage_amaru_target(
    *,
    version: str,
    source_revision: str,
    coverage_harness_sha256: str,
    registry_root: str | Path | None = None,
) -> dict[str, Any]:
    if not SHA40.fullmatch(source_revision):
        raise MeasurementTargetError("coverage Amaru target requires an exact source revision")
    if not SHA64.fullmatch(coverage_harness_sha256):
        raise MeasurementTargetError("coverage Amaru target requires an exact harness sha256")
    path = coverage_target_record_path(
        "amaru",
        source_revision,
        coverage_harness_sha256,
        registry_root=registry_root,
    )
    if not path.is_file():
        raise MeasurementTargetError(
            f"coverage Amaru target is not built for source {source_revision} "
            f"and harness {coverage_harness_sha256}"
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MeasurementTargetError(f"cannot read coverage target record {path}: {error}") from error
    return validate_coverage_amaru_target_record(
        record,
        version=version,
        source_revision=source_revision,
        coverage_harness_sha256=coverage_harness_sha256,
    )


def resolve_coverage_cardano_target(
    *,
    version: str,
    source_revision: str,
    coverage_harness_sha256: str,
    registry_root: str | Path | None = None,
) -> dict[str, Any]:
    if not SHA40.fullmatch(source_revision):
        raise MeasurementTargetError("coverage Cardano target requires an exact source revision")
    if not SHA64.fullmatch(coverage_harness_sha256):
        raise MeasurementTargetError("coverage Cardano target requires an exact harness sha256")
    path = coverage_target_record_path(
        "cardano-node",
        source_revision,
        coverage_harness_sha256,
        registry_root=registry_root,
    )
    if not path.is_file():
        raise MeasurementTargetError(
            f"coverage Cardano target is not built for source {source_revision} "
            f"and harness {coverage_harness_sha256}"
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MeasurementTargetError(f"cannot read coverage target record {path}: {error}") from error
    return validate_coverage_cardano_target_record(
        record,
        version=version,
        source_revision=source_revision,
        coverage_harness_sha256=coverage_harness_sha256,
    )


def validate_coverage_cardano_target_record(
    record: dict[str, Any],
    *,
    version: str | None = None,
    source_revision: str | None = None,
    coverage_harness_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise MeasurementTargetError("coverage target record is not schema version 1")
    for key, expected in (
        ("implementation", "cardano-node"),
        ("mode", "coverage"),
        ("engine", "GHC HPC corpus replay"),
        ("performance_authority", "non-authoritative"),
        ("non_authoritative_performance", True),
    ):
        _require(record, key, expected)
    record_revision = str(record.get("source_revision") or "")
    record_harness = str(record.get("coverage_harness_sha256") or "")
    if not str(record.get("version") or ""):
        raise MeasurementTargetError("coverage target version is not exact")
    if not SHA40.fullmatch(record_revision):
        raise MeasurementTargetError("coverage target source revision is not exact")
    if not SHA64.fullmatch(record_harness):
        raise MeasurementTargetError("coverage target harness digest is not immutable sha256")
    for key, expected in (
        ("version", version),
        ("source_revision", source_revision),
        ("coverage_harness_sha256", coverage_harness_sha256),
    ):
        if expected is not None:
            _require(record, key, expected)
    if not DIGEST.fullmatch(str(record.get("build_result_sha256") or "")):
        raise MeasurementTargetError("coverage target build-result digest is not immutable sha256")
    target_names = record.get("target_names")
    executables = record.get("executables")
    if not isinstance(target_names, list) or not target_names:
        raise MeasurementTargetError("coverage target names are absent")
    if not isinstance(executables, list) or not executables:
        raise MeasurementTargetError("coverage target executables are absent")
    for executable in executables:
        if executable.get("target_name") not in target_names:
            raise MeasurementTargetError("coverage executable target is not declared")
        if not SHA64.fullmatch(str(executable.get("sha256") or "")):
            raise MeasurementTargetError("coverage executable digest is not immutable sha256")
        path = Path(str(executable.get("path") or ""))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise MeasurementTargetError("coverage executable path is not bounded")
    state_ref = str(record.get("coverage_target_ref") or "")
    if not state_ref.startswith("state:measurement-target-builds/"):
        raise MeasurementTargetError("coverage target reference is not a portable state reference")
    serialized = json.dumps(record, sort_keys=True)
    if any(marker in serialized for marker in ("/home/", "/Users/", '"~')):
        raise MeasurementTargetError("coverage target contains a machine-specific path")
    return dict(record)


def validate_coverage_amaru_target_record(
    record: dict[str, Any],
    *,
    version: str | None = None,
    source_revision: str | None = None,
    coverage_harness_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate a portable executable identity before it enters run evidence."""
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise MeasurementTargetError("coverage target record is not schema version 1")
    for key, expected in (
        ("implementation", "amaru"),
        ("mode", "coverage"),
        ("engine", "cargo-fuzz/libFuzzer"),
        ("performance_authority", "non-authoritative"),
        ("non_authoritative_performance", True),
    ):
        _require(record, key, expected)
    record_version = str(record.get("version") or "")
    record_revision = str(record.get("source_revision") or "")
    record_harness = str(record.get("coverage_harness_sha256") or "")
    if not record_version:
        raise MeasurementTargetError("coverage target version is not exact")
    if not SHA40.fullmatch(record_revision):
        raise MeasurementTargetError("coverage target source revision is not exact")
    if not SHA64.fullmatch(record_harness):
        raise MeasurementTargetError("coverage target harness digest is not immutable sha256")
    for key, expected in (
        ("version", version),
        ("source_revision", source_revision),
        ("coverage_harness_sha256", coverage_harness_sha256),
    ):
        if expected is not None:
            _require(record, key, expected)
    for key in ("executable_digest", "build_result_sha256"):
        if not DIGEST.fullmatch(str(record.get(key) or "")):
            raise MeasurementTargetError(f"coverage target {key} is not immutable sha256")
    state_ref = str(record.get("coverage_target_ref") or "")
    if not state_ref.startswith("state:measurement-target-builds/"):
        raise MeasurementTargetError("coverage target reference is not a portable state reference")
    for key in ("working_dir", "fuzz_dir", "binary"):
        value = Path(str(record.get(key) or ""))
        if value.is_absolute() or ".." in value.parts or not value.parts:
            raise MeasurementTargetError(f"coverage target {key} is not a bounded relative path")
    serialized = json.dumps(record, sort_keys=True)
    if any(marker in serialized for marker in ("/home/", "/Users/", '"~')):
        raise MeasurementTargetError("coverage target contains a machine-specific path")
    return dict(record)
