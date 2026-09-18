"""Fail-closed resolution of locally built, revision-locked measurement targets."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class MeasurementTargetError(ValueError):
    """A requested instrumented target is absent or does not match its audit identity."""


def measurement_target_registry_root() -> Path:
    explicit = os.environ.get("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", "").strip()
    if explicit:
        return Path(explicit)
    state = os.environ.get("ADA2_DWARF_STATE_DIR", "").strip()
    if state:
        return Path(state) / "measurement-targets"
    return Path.home() / ".local" / "share" / "dwarf" / "state" / "measurement-targets"


def measurement_target_record_path(
    implementation: str,
    source_revision: str,
    patch_set_sha256: str,
    *,
    registry_root: str | Path | None = None,
) -> Path:
    root = Path(registry_root) if registry_root is not None else measurement_target_registry_root()
    return root / implementation / source_revision / f"{patch_set_sha256}.json"


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
    return record
