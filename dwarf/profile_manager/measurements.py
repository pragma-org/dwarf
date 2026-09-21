"""Validation and reference resolution for first-class DWARF measurements."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


_SPEC_ROOT = Path(__file__).resolve().parents[1] / "spec" / "v1"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MEASUREMENT_SCHEMA = _SPEC_ROOT / "measurement.schema.json"
_PROFILE_SCHEMA = _SPEC_ROOT / "measurement-profile.schema.json"


def _load_schema(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain an object")
    return document


def _validation_message(exc: ValidationError) -> str:
    location = ".".join(str(part) for part in exc.absolute_path)
    return f"{location}: {exc.message}" if location else exc.message


def _validate_schema(document: dict[str, Any], schema_path: Path) -> None:
    from profile_manager.data.catalog_definitions import InvalidDefinitionError

    validator = Draft202012Validator(_load_schema(schema_path))
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        raise InvalidDefinitionError(_validation_message(errors[0]))


def validate_measurement_definition(document: dict[str, Any]) -> dict[str, Any]:
    """Validate one measurement plus security-specific non-gating invariants."""
    from profile_manager.data.catalog_definitions import InvalidDefinitionError

    _validate_schema(document, _MEASUREMENT_SCHEMA)
    output_schema = (_REPOSITORY_ROOT / document["output_schema"]).resolve()
    if (
        _REPOSITORY_ROOT not in output_schema.parents
        or not output_schema.is_file()
    ):
        raise InvalidDefinitionError(
            "output_schema must be a safe existing repository-relative file"
        )
    mode = document["collection_mode"]
    target_modes = set(document["compatibility"]["target_modes"])
    if mode == "compiler-coverage" and target_modes != {"coverage"}:
        raise InvalidDefinitionError("compiler-coverage requires target_modes [coverage]")
    if mode == "patched-node" and target_modes != {"patched"}:
        raise InvalidDefinitionError("patched-node requires target_modes [patched]")
    if mode in {"compiler-coverage", "patched-node"} and document["default_enabled"]:
        raise InvalidDefinitionError(f"{mode} measurements cannot be default_enabled")

    failure_behavior = document["collector"]["failure_behavior"]
    threshold = document["threshold_gate"]
    if failure_behavior == "fail-run" and not threshold["supported"]:
        raise InvalidDefinitionError("fail-run requires explicit threshold support")
    if threshold["default_enabled"]:
        raise InvalidDefinitionError("threshold_gate.default_enabled must be false")

    for artifact in document["emitted_artifacts"]:
        parts = Path(artifact).parts
        if artifact.startswith("/") or ".." in parts:
            raise InvalidDefinitionError("emitted_artifacts must be safe bundle-relative paths")
    return document


def validate_measurement_profile(
    document: dict[str, Any],
    *,
    measurement_lookup: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate selections and prove every referenced tap is compatible."""
    from profile_manager.data.catalog_definitions import (
        CatalogError,
        InvalidDefinitionError,
        load_definition,
    )

    _validate_schema(document, _PROFILE_SCHEMA)
    if measurement_lookup is None:
        measurement_lookup = lambda measurement_id: load_definition(
            "measurements", measurement_id
        ).data

    seen: set[str] = set()
    profile_modes = set(document["target_modes"])
    for selection in document["measurements"]:
        measurement_id = selection["id"]
        if measurement_id in seen:
            raise InvalidDefinitionError(f"duplicate measurement {measurement_id}")
        seen.add(measurement_id)
        try:
            measurement = measurement_lookup(measurement_id)
        except CatalogError as exc:
            raise InvalidDefinitionError(f"unknown measurement {measurement_id}") from exc

        implementation = measurement["compatibility"]["implementation"]
        if document["implementation"] not in {implementation, "mixed"}:
            raise InvalidDefinitionError(
                f"measurement {measurement_id} is for {implementation}, not {document['implementation']}"
            )
        if selection["enabled"] and not (
            profile_modes & set(measurement["compatibility"]["target_modes"])
        ):
            raise InvalidDefinitionError(
                f"measurement {measurement_id} has no compatible target mode"
            )
        if (
            selection["threshold_gate"]["enabled"]
            and not measurement["threshold_gate"]["supported"]
        ):
            raise InvalidDefinitionError(
                f"measurement {measurement_id} does not support threshold gating"
            )
        if (
            selection["threshold_gate"]["enabled"]
            and not selection["threshold_gate"]["thresholds"]
        ):
            raise InvalidDefinitionError(
                f"measurement {measurement_id} requires at least one threshold"
            )
    return document
