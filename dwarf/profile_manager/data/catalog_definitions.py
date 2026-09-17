"""Safe, shared access to DWARF's scenario, target, and profile definitions."""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_CATALOGS = {"scenarios", "targets", "profiles"}
_TARGET_DECODER_TYPES = {
    "CBOR codec",
    "Mini-protocol decoder",
    "Coverage-guided AFL++ harness (native SanitizerCoverage)",
}


class CatalogError(ValueError):
    """Base error for a rejected catalog request."""


class UnknownCatalogError(CatalogError):
    pass


class UnsafeDefinitionIdError(CatalogError):
    pass


class DefinitionNotFoundError(CatalogError):
    pass


class InvalidDefinitionError(CatalogError):
    pass


@dataclass(frozen=True)
class DefinitionRecord:
    catalog: str
    definition_id: str
    path: Path
    source: bytes
    data: dict[str, Any]

    @property
    def download_filename(self) -> str:
        return "profile.yaml" if self.catalog == "profiles" else f"{self.definition_id}.yaml"

    @property
    def archive_path(self) -> str:
        if self.catalog == "scenarios":
            return f"dwarf/scenarios/{self.definition_id}.yaml"
        if self.catalog == "targets":
            return f"dwarf/targets/manifests/{self.definition_id}.yaml"
        return f"dwarf/profiles/{self.definition_id}/profile.yaml"


def catalog_root(catalog: str) -> Path:
    """Return the configured root for one allow-listed catalog."""
    if catalog not in _CATALOGS:
        raise UnknownCatalogError(catalog)
    dwarf_root = Path(__file__).resolve().parents[2]
    if catalog == "scenarios":
        return Path(os.environ.get("ADA2_DWARF_SCENARIOS_DIR") or dwarf_root / "scenarios")
    if catalog == "targets":
        return Path(
            os.environ.get("ADA2_DWARF_MANIFESTS_DIR")
            or dwarf_root / "targets" / "manifests"
        )
    return Path(os.environ.get("ADA2_DWARF_PROFILES_DIR") or dwarf_root / "profiles")


def validate_definition_id(definition_id: str) -> str:
    """Validate an identifier before using it in any filesystem operation."""
    if (
        not _ID_RE.fullmatch(definition_id or "")
        or definition_id.startswith("._")
        or definition_id in {".DS_Store", ".", ".."}
    ):
        raise UnsafeDefinitionIdError(definition_id)
    return definition_id


def definition_path(catalog: str, definition_id: str) -> Path:
    """Resolve one definition path and prove that it stays under its catalog root."""
    definition_id = validate_definition_id(definition_id)
    root = catalog_root(catalog).resolve()
    candidate = (
        root / definition_id / "profile.yaml"
        if catalog == "profiles"
        else root / f"{definition_id}.yaml"
    ).resolve()
    if root != candidate and root not in candidate.parents:
        raise UnsafeDefinitionIdError(definition_id)
    return candidate


def _parse_source(source: bytes) -> dict[str, Any]:
    try:
        data = yaml.safe_load(source.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InvalidDefinitionError(str(exc)) from exc
    if not isinstance(data, dict):
        raise InvalidDefinitionError("definition root must be an object")
    return data


def _validate_loaded(catalog: str, definition_id: str, data: dict[str, Any]) -> None:
    if data.get("id") != definition_id:
        raise InvalidDefinitionError("definition id does not match its filename")
    try:
        if catalog == "scenarios":
            from profile_manager import scenario

            body = (json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8")
            report = scenario.validate_scenario_body(body)
            if not report.get("ok"):
                raise InvalidDefinitionError(report.get("error") or "invalid scenario")
        elif catalog == "profiles":
            validate_profile_definition(data)
        else:
            validate_target_manifest(data)
    except InvalidDefinitionError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidDefinitionError(str(exc)) from exc


def validate_target_manifest(data: dict[str, Any]) -> None:
    """Validate the stable target fields needed by the runner and dashboard."""
    for key in (
        "id", "binary", "input_format", "implementation", "language",
        "upstream_commit", "decoder_type",
    ):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise InvalidDefinitionError(f"{key} must be a non-empty string")
    if data["implementation"] not in {"amaru", "cardano-node"}:
        raise InvalidDefinitionError("unsupported target implementation")
    if data["input_format"] not in {"stdin_bytes", "file_path", "file_arg", "argv"}:
        raise InvalidDefinitionError("unsupported target input_format")
    if data["decoder_type"] not in _TARGET_DECODER_TYPES:
        raise InvalidDefinitionError("unsupported target decoder_type")
    if "invariants" not in data or (
        not isinstance(data["invariants"], list)
        or not all(isinstance(value, str) for value in data["invariants"])
    ):
        raise InvalidDefinitionError("invariants must be a list of strings")
    if "complements" in data and not (
        isinstance(data["complements"], str)
        or (
            isinstance(data["complements"], list)
            and all(isinstance(value, str) for value in data["complements"])
        )
    ):
        raise InvalidDefinitionError("complements must be a string or list of strings")
    if "expected_outcomes" in data and not isinstance(data["expected_outcomes"], dict):
        raise InvalidDefinitionError("expected_outcomes must be an object")
    if "harness" in data and not isinstance(data["harness"], dict):
        raise InvalidDefinitionError("harness must be an object")
    for key in ("status", "source_caveat"):
        if key in data and not isinstance(data[key], str):
            raise InvalidDefinitionError(f"{key} must be a string")


def validate_profile_definition(data: dict[str, Any]) -> None:
    """Validate the persisted profile contract before normalizing runtime fields."""
    from profile_manager.profiles import Profile

    for key in ("id", "label"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise InvalidDefinitionError(f"{key} must be a non-empty string")
    validate_definition_id(data["id"])
    if isinstance(data.get("network_magic"), bool):
        raise InvalidDefinitionError("network_magic must be an integer")
    try:
        magic = int(data["network_magic"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidDefinitionError("network_magic must be an integer") from exc
    if magic < 0:
        raise InvalidDefinitionError("network_magic must be non-negative")
    if not isinstance(data.get("peer_sharing"), bool):
        raise InvalidDefinitionError("peer_sharing must be a boolean")
    if "node_type" in data and data["node_type"] not in {"cardano-node", "amaru", "mixed"}:
        raise InvalidDefinitionError("unsupported node_type")
    Profile.from_dict(data)


def load_definition(catalog: str, definition_id: str) -> DefinitionRecord:
    path = definition_path(catalog, definition_id)
    if not path.is_file():
        raise DefinitionNotFoundError(definition_id)
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise DefinitionNotFoundError(definition_id) from exc
    data = _parse_source(source)
    _validate_loaded(catalog, definition_id, data)
    return DefinitionRecord(catalog, definition_id, path, source, data)


def parse_and_validate_definition(
    catalog: str,
    source: bytes,
    *,
    expected_id: str | None = None,
) -> dict[str, Any]:
    """Parse JSON or YAML and validate it without touching the filesystem."""
    if catalog not in _CATALOGS:
        raise UnknownCatalogError(catalog)
    data = _parse_source(source)
    definition_id = data.get("id")
    if not isinstance(definition_id, str):
        raise InvalidDefinitionError("id must be a string")
    validate_definition_id(definition_id)
    if expected_id is not None and definition_id != expected_id:
        raise InvalidDefinitionError("definition id is immutable and must match the URL")
    _validate_loaded(catalog, definition_id, data)
    if catalog == "scenarios":
        from profile_manager import scenario

        body = (json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8")
        current_errors = set(scenario.semantic_validate_scenario_body(body)["errors"])
        baseline_errors: set[str] = set()
        if expected_id is not None:
            path = definition_path(catalog, expected_id)
            if path.is_file():
                try:
                    baseline_data = _parse_source(path.read_bytes())
                    baseline_body = (
                        json.dumps(baseline_data, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    baseline_errors = set(
                        scenario.semantic_validate_scenario_body(baseline_body)["errors"]
                    )
                except (OSError, CatalogError):
                    baseline_errors = set()
        introduced = sorted(current_errors - baseline_errors)
        if introduced:
            raise InvalidDefinitionError("; ".join(introduced))
    return data


def serialize_definition(data: dict[str, Any]) -> bytes:
    """Return DWARF's stable JSON-shaped YAML representation."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def save_definition(
    catalog: str,
    definition_id: str,
    source: bytes,
    *,
    create: bool,
) -> DefinitionRecord:
    """Validate and atomically create or replace one allow-listed definition."""
    definition_id = validate_definition_id(definition_id)
    data = parse_and_validate_definition(catalog, source, expected_id=definition_id)
    path = definition_path(catalog, definition_id)
    if create and path.exists():
        raise InvalidDefinitionError("definition already exists")
    if not create and not path.is_file():
        raise DefinitionNotFoundError(definition_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = serialize_definition(data)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return load_definition(catalog, definition_id)


def _candidate_ids(catalog: str) -> Iterable[str]:
    root = catalog_root(catalog)
    if not root.is_dir():
        return []
    if catalog == "profiles":
        return sorted(path.parent.name for path in root.glob("*/profile.yaml") if path.is_file())
    return sorted(path.stem for path in root.glob("*.yaml") if path.is_file())


def list_definitions(catalog: str) -> list[DefinitionRecord]:
    """Return every safe, valid definition; malformed side files stay invisible."""
    records: list[DefinitionRecord] = []
    for definition_id in _candidate_ids(catalog):
        try:
            records.append(load_definition(catalog, definition_id))
        except CatalogError:
            continue
    return records


def deterministic_catalog_archive(catalog: str) -> bytes:
    """Create a reproducible tar.gz containing only validated definitions."""
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for record in list_definitions(catalog):
                info = tarfile.TarInfo(record.archive_path)
                info.size = len(record.source)
                info.mtime = 0
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                archive.addfile(info, io.BytesIO(record.source))
    return output.getvalue()


def archive_filename(catalog: str) -> str:
    if catalog not in _CATALOGS:
        raise UnknownCatalogError(catalog)
    return f"dwarf-{catalog}.tar.gz"


def catalog_label(catalog: str) -> str:
    if catalog not in _CATALOGS:
        raise UnknownCatalogError(catalog)
    return {"scenarios": "Scenario", "targets": "Target", "profiles": "Profile"}[catalog]
