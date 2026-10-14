"""Deterministic packaged release and catalog identity generation."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


GIT_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
CATALOG_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SCHEMA_VERSION = 1


class CatalogIdentityError(ValueError):
    """The packaged catalog cannot be represented truthfully."""


def catalog_snapshot(scenarios_dir: Path, primitive_registry: Path) -> dict:
    """Hash stable catalog-relative paths and exact file bytes."""
    scenarios_dir = Path(scenarios_dir)
    primitive_registry = Path(primitive_registry)
    scenario_paths = sorted(scenarios_dir.glob("*.yaml"))
    if not scenarios_dir.is_dir():
        raise CatalogIdentityError(f"scenario directory is missing: {scenarios_dir}")
    if not primitive_registry.is_file():
        raise CatalogIdentityError(f"primitive registry is missing: {primitive_registry}")
    try:
        registry = json.loads(primitive_registry.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise CatalogIdentityError("primitive registry is invalid JSON") from exc
    primitives = registry.get("primitives") if isinstance(registry, dict) else None
    if not isinstance(primitives, dict):
        raise CatalogIdentityError("primitive registry must contain a primitives object")

    digest = hashlib.sha256(b"DWARF-CATALOG-V1\0")
    entries = [
        (f"scenarios/{path.name}", path)
        for path in scenario_paths
    ]
    entries.append(("primitives/registry.json", primitive_registry))
    try:
        for relative, path in entries:
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    except OSError as exc:
        raise CatalogIdentityError(f"catalog file cannot be read: {exc.filename}") from exc
    return {
        "catalog_digest": f"sha256:{digest.hexdigest()}",
        "scenario_count": len(scenario_paths),
        "primitive_count": len(primitives),
        "scenario_names": [path.name for path in scenario_paths],
    }


def generate_release_identity(
    *,
    dwarf_root: Path,
    output: Path,
    component_role: str,
    release_revision: str,
    catalog_revision: str,
) -> dict:
    if component_role not in {"dashboard", "controller"}:
        raise CatalogIdentityError("component role must be dashboard or controller")
    if not GIT_REVISION_RE.fullmatch(release_revision):
        raise CatalogIdentityError("release revision must be a full lowercase Git SHA")
    if not GIT_REVISION_RE.fullmatch(catalog_revision):
        raise CatalogIdentityError("catalog revision must be a full lowercase Git SHA")
    dwarf_root = Path(dwarf_root)
    snapshot = catalog_snapshot(
        dwarf_root / "scenarios", dwarf_root / "primitives" / "registry.json"
    )
    identity = {
        "schema_version": SCHEMA_VERSION,
        "component_role": component_role,
        "release_revision": release_revision,
        "catalog_revision": catalog_revision,
        "catalog_digest": snapshot["catalog_digest"],
        "packaged_scenario_count": snapshot["scenario_count"],
        "packaged_primitive_count": snapshot["primitive_count"],
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return identity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--dwarf-root", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--component-role", required=True)
    generate.add_argument("--release-revision", required=True)
    generate.add_argument("--catalog-revision", required=True)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        generate_release_identity(
            dwarf_root=args.dwarf_root,
            output=args.output,
            component_role=args.component_role,
            release_revision=args.release_revision,
            catalog_revision=args.catalog_revision,
        )
    except CatalogIdentityError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
