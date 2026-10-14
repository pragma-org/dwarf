"""Safe, shared access to DWARF's first-class catalog definitions."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import stat
import tarfile
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import fcntl
from pathlib import Path
from typing import Any, Iterable

import yaml


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_CATALOGS = {
    "scenarios",
    "targets",
    "profiles",
    "measurements",
    "measurement-profiles",
}
_USER_DELETABLE_CATALOGS = {
    "scenarios",
    "measurements",
    "measurement-profiles",
    "profiles",
}
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


class DefinitionConflictError(CatalogError):
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
        if self.catalog == "measurements":
            return f"dwarf/measurements/{self.definition_id}.yaml"
        if self.catalog == "measurement-profiles":
            return f"dwarf/measurement-profiles/{self.definition_id}.yaml"
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
    if catalog == "measurements":
        return Path(
            os.environ.get("ADA2_DWARF_MEASUREMENTS_DIR")
            or dwarf_root / "measurements"
        )
    if catalog == "measurement-profiles":
        return Path(
            os.environ.get("ADA2_DWARF_MEASUREMENT_PROFILES_DIR")
            or dwarf_root / "measurement-profiles"
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
        elif catalog == "measurements":
            from profile_manager.measurements import validate_measurement_definition

            validate_measurement_definition(data)
        elif catalog == "measurement-profiles":
            from profile_manager.measurements import validate_measurement_profile

            validate_measurement_profile(data)
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


@contextmanager
def _catalog_write_lock(catalog: str):
    """Serialize catalog mutations across dashboard workers and processes."""
    root = catalog_root(catalog)
    root.mkdir(parents=True, exist_ok=True)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    root_descriptor = None
    descriptor = None
    try:
        root_descriptor = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow,
        )
        descriptor = os.open(
            ".dwarf-catalog.lock",
            os.O_RDWR | os.O_CREAT | nofollow,
            0o600,
            dir_fd=root_descriptor,
        )
    except OSError as exc:
        if root_descriptor is not None:
            os.close(root_descriptor)
        raise UnsafeDefinitionIdError("unsafe catalog root or lock") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise UnsafeDefinitionIdError("unsafe catalog lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield root_descriptor
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            os.close(root_descriptor)


@contextmanager
def _catalog_write_locks(*catalogs: str):
    """Hold several catalog locks in one fixed (sorted) order to avoid deadlock."""
    with ExitStack() as stack:
        roots = {}
        for catalog in sorted(set(catalogs)):
            roots[catalog] = stack.enter_context(_catalog_write_lock(catalog))
        yield roots


def scenario_reference_lock(data: Any, *, scenarios: bool = True):
    """Lock a scenario write against deletion of the user profiles it names.

    Only user-created measurement and topology profiles can be deleted, so
    packaged, ``none`` and absent references never touch (or create) a profile
    catalog lock.
    """
    catalogs = ["scenarios"] if scenarios else []
    for field, catalog in (
        ("measurement_profile", "measurement-profiles"),
        ("profile", "profiles"),
    ):
        profile = data.get(field) if isinstance(data, dict) else None
        if (
            isinstance(profile, str)
            and profile != "none"
            and _ID_RE.fullmatch(profile)
            and not packaged_definition_path(catalog, profile).is_file()
        ):
            catalogs.append(catalog)
    return _catalog_write_locks(*catalogs)


def validate_live_scenario_references(data: Any) -> None:
    """Reject live scenario writes whose mutable catalog references disappeared.

    Pending drafts may retain unresolved references.  This check is therefore
    called only while saving or promoting into the live scenario catalog and
    while the matching catalog locks are held.
    """
    if not isinstance(data, dict):
        return
    for field, catalog in (
        ("measurement_profile", "measurement-profiles"),
        ("profile", "profiles"),
    ):
        value = data.get(field)
        if value is None or value == "none":
            continue
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise InvalidDefinitionError(f"invalid {field} {value!r}")
        if not (
            definition_path(catalog, value).is_file()
            or packaged_definition_path(catalog, value).is_file()
        ):
            raise InvalidDefinitionError(f"unknown {field} {value!r}")


def _scenarios_referencing_profile(
    profile_id: str, field: str = "measurement_profile"
) -> list[str]:
    """Return live catalog scenario ids whose ``field`` names ``profile_id``."""
    root = catalog_root("scenarios")
    if not root.is_dir():
        return []
    users = []
    for path in sorted(root.glob("*.yaml")):
        try:
            data = _parse_source(path.read_bytes())
        except (OSError, CatalogError):
            continue
        if data.get(field) == profile_id:
            users.append(path.stem)
    return users


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
    rendered = serialize_definition(data)
    lock = (
        scenario_reference_lock(data) if catalog == "scenarios" else _catalog_write_lock(catalog)
    )
    with lock:
        if catalog == "scenarios":
            # A profile delete may have finished while we waited for the lock.
            _validate_loaded(catalog, definition_id, data)
            validate_live_scenario_references(data)
        if create and path.exists():
            raise InvalidDefinitionError("definition already exists")
        if not create and not path.is_file():
            raise DefinitionNotFoundError(definition_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
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


def packaged_definition_path(catalog: str, definition_id: str) -> Path:
    """Return the immutable source-tree path for one supported catalog id."""
    definition_id = validate_definition_id(definition_id)
    dwarf_root = Path(__file__).resolve().parents[2]
    roots = {
        "scenarios": dwarf_root / "scenarios",
        "targets": dwarf_root / "targets" / "manifests",
        "profiles": dwarf_root / "profiles",
        "measurements": dwarf_root / "measurements",
        "measurement-profiles": dwarf_root / "measurement-profiles",
    }
    try:
        root = roots[catalog]
    except KeyError as exc:
        raise UnknownCatalogError(catalog) from exc
    return (
        root / definition_id / "profile.yaml"
        if catalog == "profiles"
        else root / f"{definition_id}.yaml"
    )


def is_user_definition(catalog: str, definition_id: str) -> bool:
    """True only for an existing writable definition without a packaged peer."""
    if catalog not in _USER_DELETABLE_CATALOGS:
        return False
    try:
        path = definition_path(catalog, definition_id)
    except UnsafeDefinitionIdError:
        return False
    candidate = _user_definition_candidate(catalog, definition_id)
    return (
        path == candidate
        and path.is_file()
        and not packaged_definition_path(catalog, definition_id).is_file()
    )


def _user_definition_candidate(catalog: str, definition_id: str) -> Path | None:
    """Return the unresolved definition path, or None if any component is a symlink."""
    root = catalog_root(catalog).resolve()
    if catalog == "profiles":
        directory = root / definition_id
        if directory.is_symlink():
            return None
        candidate = directory / "profile.yaml"
    else:
        candidate = root / f"{definition_id}.yaml"
    return None if candidate.is_symlink() else candidate


def delete_user_measurement(
    definition_id: str, *, expected_sha256: str
) -> None:
    """Delete one digest-bound user measurement; packaged definitions are immutable."""
    delete_user_definition(
        "measurements", definition_id, expected_sha256=expected_sha256
    )


@contextmanager
def _opened_user_definition_for_delete(
    catalog: str,
    definition_id: str,
    candidate: Path,
    *,
    root_descriptor: int,
):
    """Open a definition through stable directory descriptors for safe unlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with ExitStack() as descriptors:
        directory_descriptor = None
        try:
            if catalog == "profiles":
                directory_descriptor = os.open(
                    definition_id,
                    flags | getattr(os, "O_DIRECTORY", 0),
                    dir_fd=root_descriptor,
                )
                descriptors.callback(os.close, directory_descriptor)
                opened_directory = os.fstat(directory_descriptor)
                descriptor = os.open(
                    "profile.yaml", flags, dir_fd=directory_descriptor
                )
            else:
                opened_directory = None
                descriptor = os.open(
                    f"{definition_id}.yaml", flags, dir_fd=root_descriptor
                )
            descriptors.callback(os.close, descriptor)
        except OSError as exc:
            raise DefinitionNotFoundError(definition_id) from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise UnsafeDefinitionIdError(definition_id)
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        yield (
            b"".join(chunks),
            opened,
            opened_directory,
            root_descriptor,
            directory_descriptor,
        )


def delete_user_definition(
    catalog: str,
    definition_id: str,
    *,
    expected_sha256: str,
    blocker=None,
) -> None:
    """Delete one digest-bound user measurement, measurement or topology profile.

    Only the validated definition file is removed; a topology profile's directory
    is removed afterwards only when it is already empty (sidecars are retained).
    Running topologies are the caller's concern and are never touched here.
    """
    if catalog not in _USER_DELETABLE_CATALOGS:
        raise UnknownCatalogError(catalog)
    noun = catalog_label(catalog).lower()
    stale = f"{noun} changed; reload before deleting"
    definition_id = validate_definition_id(definition_id)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
        raise DefinitionConflictError(stale)
    if packaged_definition_path(catalog, definition_id).is_file():
        raise DefinitionConflictError(f"packaged {noun}s cannot be deleted")

    catalogs = (
        (catalog, "scenarios")
        if catalog in {"measurement-profiles", "profiles"}
        else (catalog,)
    )
    with _catalog_write_locks(*catalogs) as locked_roots:
        if blocker is not None:
            reason = blocker(definition_id)
            if reason:
                raise DefinitionConflictError(str(reason))
        candidate = _user_definition_candidate(catalog, definition_id)
        resolved = definition_path(catalog, definition_id)
        if candidate is None or resolved != candidate:
            raise UnsafeDefinitionIdError(definition_id)
        if not candidate.is_file():
            raise DefinitionNotFoundError(definition_id)

        with _opened_user_definition_for_delete(
            catalog,
            definition_id,
            candidate,
            root_descriptor=locked_roots[catalog],
        ) as opened_definition:
            (
                source,
                opened,
                opened_directory,
                root_descriptor,
                directory_descriptor,
            ) = opened_definition
            data = _parse_source(source)
            _validate_loaded(catalog, definition_id, data)
            if hashlib.sha256(source).hexdigest() != expected_sha256:
                raise DefinitionConflictError(stale)
            if catalog in {"measurement-profiles", "profiles"}:
                users = _scenarios_referencing_profile(
                    definition_id,
                    (
                        "measurement_profile"
                        if catalog == "measurement-profiles"
                        else "profile"
                    ),
                )
                if users:
                    raise DefinitionConflictError(
                        f"{noun} is used by live scenarios: " + ", ".join(users)
                    )
            latest = (
                os.stat(
                    "profile.yaml",
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if catalog == "profiles"
                else os.stat(
                    f"{definition_id}.yaml",
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            )
            if (
                stat.S_ISLNK(latest.st_mode)
                or latest.st_dev != opened.st_dev
                or latest.st_ino != opened.st_ino
                or latest.st_size != opened.st_size
            ):
                raise DefinitionConflictError(stale)
            if catalog == "profiles":
                current_directory = os.stat(
                    definition_id,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISDIR(current_directory.st_mode)
                    or current_directory.st_dev != opened_directory.st_dev
                    or current_directory.st_ino != opened_directory.st_ino
                ):
                    raise DefinitionConflictError(stale)
                os.unlink("profile.yaml", dir_fd=directory_descriptor)
                try:
                    os.rmdir(definition_id, dir_fd=root_descriptor)
                except OSError:
                    pass  # retain unrelated sidecars; never remove recursively
            else:
                os.unlink(f"{definition_id}.yaml", dir_fd=root_descriptor)
    if catalog == "scenarios":
        from profile_manager.data.scenarios import invalidate_scenario_cache

        invalidate_scenario_cache()


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
    return {
        "scenarios": "Scenario",
        "targets": "Target",
        "profiles": "Profile",
        "measurements": "Measurement",
        "measurement-profiles": "Measurement profile",
    }[catalog]
