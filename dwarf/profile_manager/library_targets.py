"""Resolve trusted library target recipes without building them."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DWARF_ROOT = ROOT / "dwarf"
_TARGET_ID = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_PLATFORM = re.compile(r"^linux/(?:amd64|arm64)$")
_REPOSITORIES = {
    "amaru": "https://github.com/pragma-org/amaru.git",
}
_BUILDER_TRANSFORM = "amaru-library-target-v1"


class LibraryTargetError(ValueError):
    """A trusted library target recipe cannot be bound to the selection."""


def _sha256(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def library_requirements_digest(requirements: list[dict[str, Any]]) -> str:
    if not isinstance(requirements, list) or not requirements:
        raise LibraryTargetError("library target requirements are missing")
    return _sha256(_canonical(requirements))


def resolve_controller_requirements(
    *,
    scenario_id: str,
    version: str,
    source_revision: str,
    platform: str,
    expected_digest: str,
) -> list[dict[str, Any]]:
    """Independently re-resolve one controller request from active catalogs."""

    from profile_manager.run_plan import RunPlanRequest, resolve_run_plan

    architecture = platform.removeprefix("linux/")
    plan = resolve_run_plan(
        RunPlanRequest(
            scenario_id=scenario_id,
            version_policy="exact",
            amaru_version=version,
            acknowledge_unknown_versions=True,
        ),
        capability_context={"host_os": "linux", "host_arch": architecture},
    )
    target = (plan.get("scenario") or {}).get("target") or {}
    requirements = (plan.get("scenario") or {}).get(
        "library_target_requirements"
    ) or []
    if (
        target.get("implementation") != "amaru"
        or target.get("version") != version
        or target.get("source_revision") != source_revision
        or library_requirements_digest(requirements) != expected_digest
    ):
        raise LibraryTargetError(
            "selected Amaru library requirement differs from the active catalogs"
        )
    return requirements


def _regular_file(path: Path, *, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise LibraryTargetError(f"{label} is outside the trusted DWARF tree") from exc
    if path.is_symlink() or not resolved.is_file():
        raise LibraryTargetError(f"{label} must be a regular trusted file")
    return resolved


def _target_refs(scenario):
    sections = ("setup", "load", "faults", "probes", "assertions", "teardown")
    if scenario.phases:
        for phase_index, phase in enumerate(scenario.phases):
            for section in sections:
                for ref_index, ref in enumerate(getattr(phase, section)):
                    if "target_id" in ref.params and "manifests_dir" in ref.params:
                        yield ["phases", phase_index, section, ref_index], ref
        return
    for section in sections:
        for ref_index, ref in enumerate(getattr(scenario, section)):
            if "target_id" in ref.params and "manifests_dir" in ref.params:
                yield [section, ref_index], ref


def _recipe(target_id: str, manifests_dir: str) -> dict[str, Any]:
    if not _TARGET_ID.fullmatch(target_id):
        raise LibraryTargetError("library target id is invalid")
    if target_id != "amaru-cbor-decode-tx-body":
        raise LibraryTargetError(
            "automatic library preparation currently supports only the proven Amaru tx-body target"
        )
    if manifests_dir != "dwarf/targets/manifests":
        raise LibraryTargetError(
            "automatic library preparation requires the packaged target manifest catalog"
        )
    manifest_path = _regular_file(
        ROOT / manifests_dir / f"{target_id}.yaml",
        root=DWARF_ROOT / "targets" / "manifests",
        label="library target manifest",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryTargetError("library target manifest is invalid") from exc
    if (
        manifest.get("id") != target_id
        or manifest.get("implementation") != "amaru"
        or manifest.get("language") != "rust"
        or manifest.get("input_format", "stdin_bytes") != "stdin_bytes"
    ):
        raise LibraryTargetError("library target manifest is not an Amaru Rust stdin target")
    expected_binary = f"dwarf/targets/amaru/target/release/{target_id}"
    if manifest.get("binary") != expected_binary:
        raise LibraryTargetError("library target binary declaration is unsupported")

    crate = DWARF_ROOT / "targets" / "amaru"
    cargo_path = _regular_file(crate / "Cargo.toml", root=crate, label="library Cargo recipe")
    builder_path = _regular_file(
        DWARF_ROOT / "scripts" / "amaru_library_target_builder.py",
        root=DWARF_ROOT / "scripts",
        label="library target builder",
    )
    try:
        cargo = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
        bins = cargo.get("bin") or []
        selected = [row for row in bins if row.get("name") == target_id]
        if len(selected) != 1 or set(selected[0]) != {"name", "path"}:
            raise LibraryTargetError("library Cargo recipe does not name one exact target")
        source_relative = Path(str(selected[0]["path"]))
        if source_relative.is_absolute() or ".." in source_relative.parts:
            raise LibraryTargetError("library target source path is unsafe")
        source_path = _regular_file(
            crate / source_relative, root=crate, label="library target source"
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise LibraryTargetError("library Rust recipe is invalid") from exc

    from scripts.experimental_amaru_instrumentation import BUILDER_BASE

    inputs = []
    for path in (
        manifest_path,
        cargo_path,
        source_path,
        builder_path,
    ):
        inputs.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(path.read_bytes()),
            }
        )
    recipe = {
        "schema_version": "dwarf.library-target-recipe.v1",
        "target_id": target_id,
        "implementation": "amaru",
        "toolchain": "selected-source",
        "builder_image": BUILDER_BASE,
        "builder_transform": _BUILDER_TRANSFORM,
        "binary_name": target_id,
        "source_path": source_path.relative_to(ROOT).as_posix(),
        "template_manifest": manifest_path.relative_to(ROOT).as_posix(),
        "template_upstream_commit": str(manifest.get("upstream_commit") or ""),
        "inputs": inputs,
    }
    return {**recipe, "recipe_digest": _sha256(_canonical(recipe))}


def automatic_preparation_supported(scenario) -> bool:
    """Return true only for packaged Amaru recipes owned by this feature."""

    if scenario.runtime != "library" or scenario.target.get("implementation") != "amaru":
        return False
    refs = list(_target_refs(scenario))
    if not refs:
        return False
    for _location, ref in refs:
        target_id = str(ref.params.get("target_id") or "")
        manifests_dir = str(ref.params.get("manifests_dir") or "")
        if (
            target_id != "amaru-cbor-decode-tx-body"
            or manifests_dir != "dwarf/targets/manifests"
        ):
            return False
        _recipe(target_id, manifests_dir)
    return True


def library_target_requirements(
    scenario,
    *,
    release: dict[str, Any],
    catalog: dict[str, Any],
    platform: str,
) -> list[dict[str, Any]]:
    """Return exact source-qualified build requirements for a library scenario."""

    if scenario.runtime != "library":
        return []
    implementation = str(release.get("implementation") or "")
    version = str(release.get("version") or "")
    revision = str(release.get("source_revision") or "")
    if implementation != scenario.target.get("implementation"):
        raise LibraryTargetError("selected release implementation differs from the scenario")
    if implementation not in _REPOSITORIES:
        raise LibraryTargetError(
            f"automatic library preparation does not support {implementation}"
        )
    if not version or not _REVISION.fullmatch(revision):
        raise LibraryTargetError("selected library release has no exact source revision")
    if not _PLATFORM.fullmatch(platform):
        raise LibraryTargetError("library target platform is unsupported")
    source = str((catalog.get("sources") or {}).get(implementation) or "").removesuffix(
        ".git"
    )
    expected = _REPOSITORIES[implementation].removesuffix(".git")
    if source != expected:
        raise LibraryTargetError("selected library source repository is not trusted")

    requirements = []
    for location, ref in _target_refs(scenario):
        recipe = _recipe(
            str(ref.params.get("target_id") or ""),
            str(ref.params.get("manifests_dir") or ""),
        )
        identity = {
            "target_id": recipe["target_id"],
            "implementation": implementation,
            "version": version,
            "source_repository": _REPOSITORIES[implementation],
            "source_revision": revision,
            "platform": platform,
            "recipe_digest": recipe["recipe_digest"],
            "builder_image": recipe["builder_image"],
            "builder_transform": recipe["builder_transform"],
            "toolchain": recipe["toolchain"],
        }
        cache_key = _sha256(_canonical(identity))
        requirements.append(
            {
                **identity,
                "cache_key": cache_key,
                "location": location,
                "binary_name": recipe["binary_name"],
                "source_path": recipe["source_path"],
                "template_manifest": recipe["template_manifest"],
                "template_upstream_commit": recipe["template_upstream_commit"],
                "builder_transform": recipe["builder_transform"],
                "recipe_inputs": recipe["inputs"],
            }
        )
    if not requirements:
        raise LibraryTargetError("library scenario has no packaged target requirement")
    return requirements
