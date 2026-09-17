"""Plugin discovery and registry extension loading for Dwarf."""

from __future__ import annotations

import importlib.util
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable


DWARF_API_VERSION = "v1"
DEFAULT_PLUGIN_ROOT = Path.home() / ".dwarf" / "plugins"
PLUGINS_ENV = "DWARF_PLUGINS_DIR"


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    plugin_root: Path
    dwarf_api_version: str
    entrypoint: Path | None
    registry_path: Path | None


def _candidate_roots(plugin_roots: Iterable[Path] | None) -> list[Path]:
    roots = list(plugin_roots) if plugin_roots is not None else plugin_roots_from_env()
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = Path(root).expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def _declared_plugin_path(
    plugin_root: Path,
    raw: Any,
    *,
    field: str,
    errors: list[str],
) -> Path | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        errors.append(f"manifest field {field!r} must be a non-empty string")
        return None
    path = Path(raw)
    path = (plugin_root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(plugin_root)
    except ValueError:
        errors.append(f"declared {field} path escapes the plugin root: {raw}")
        return path
    if not path.is_file():
        errors.append(f"declared {field} does not exist as a regular file: {raw}")
    return path


def inspect_plugin_candidates(
    plugin_roots: Iterable[Path] | None = None,
) -> list[dict[str, Any]]:
    """Inspect plugin candidates without importing or executing plugin code.

    Runtime discovery intentionally remains fail-fast.  This parallel inspection
    boundary is for diagnostics and inventory: every direct child directory is
    represented, one malformed candidate cannot hide its siblings, and an
    entrypoint is never imported merely to render dashboard metadata.
    """

    candidates: list[dict[str, Any]] = []
    for configured_root in _candidate_roots(plugin_roots):
        if not configured_root.is_dir():
            continue
        for plugin_root in sorted(path for path in configured_root.iterdir() if path.is_dir()):
            plugin_root = plugin_root.resolve()
            manifest_path = plugin_root / "plugin.json"
            errors: list[str] = []
            warnings: list[str] = []
            discrepancies: list[str] = []
            payload: dict[str, Any] = {}
            manifest_loaded = False
            if not manifest_path.is_file():
                errors.append("required plugin.json manifest does not exist")
            else:
                try:
                    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(loaded, dict):
                        errors.append("plugin manifest root must be an object")
                    else:
                        payload = loaded
                        manifest_loaded = True
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"manifest parse failed: {exc}")

            raw_plugin_id = payload.get("plugin_id", plugin_root.name)
            if not isinstance(raw_plugin_id, str) or not raw_plugin_id.strip():
                errors.append("manifest plugin_id must be a non-empty string")
                plugin_id = plugin_root.name
            else:
                plugin_id = raw_plugin_id.strip()
            api_version = payload.get("dwarf_api_version")
            if manifest_loaded and api_version != DWARF_API_VERSION:
                errors.append(
                    f"plugin {plugin_id!r} targets {api_version!r}, "
                    f"expected {DWARF_API_VERSION!r}"
                )

            entrypoint = _declared_plugin_path(
                plugin_root,
                payload.get("entrypoint"),
                field="entrypoint",
                errors=errors,
            )
            registry_path = _declared_plugin_path(
                plugin_root,
                payload.get("registry"),
                field="registry",
                errors=errors,
            )
            has_entrypoint = payload.get("entrypoint") is not None
            has_registry = payload.get("registry") is not None
            if has_entrypoint and has_registry:
                load_mode = "registry-and-entrypoint"
            elif has_entrypoint:
                load_mode = "entrypoint-only"
            elif has_registry:
                load_mode = "registry-only"
            else:
                load_mode = "manifest-only"
                if manifest_loaded:
                    discrepancies.append(
                        "manifest declares neither a registry nor an entrypoint"
                    )

            registry_entries: dict[str, Any] = {}
            registry_within_root = False
            if registry_path is not None:
                try:
                    registry_path.relative_to(plugin_root)
                    registry_within_root = True
                except ValueError:
                    pass
            if (
                has_registry
                and registry_path is not None
                and registry_within_root
                and registry_path.is_file()
            ):
                try:
                    registry_payload = json.loads(registry_path.read_text(encoding="utf-8"))
                    if not isinstance(registry_payload, dict):
                        raise ValueError("registry root must be an object")
                    raw_entries = registry_payload.get("primitives") or {}
                    if not isinstance(raw_entries, dict):
                        raise ValueError("registry 'primitives' must be an object")
                    registry_entries = {
                        name: _normalize_params_schema(dict(entry), plugin_root)
                        if isinstance(entry, dict)
                        else entry
                        for name, entry in raw_entries.items()
                    }
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    errors.append(f"registry parse or shape validation failed: {exc}")

            runtime_unknown = bool(has_entrypoint)
            if runtime_unknown:
                warnings.append(
                    "runtime registration cannot be safely inventoried without executing the entrypoint"
                )
            if has_registry:
                warnings.append(
                    "declarative registry entries still require a valid module/class executor"
                )
            candidates.append(
                {
                    "plugin_id": plugin_id,
                    "directory_name": plugin_root.name,
                    "configured_root": str(configured_root),
                    "plugin_root": str(plugin_root),
                    "manifest_path": str(manifest_path.resolve()),
                    "manifest_source": payload,
                    "dwarf_api_version": api_version or "",
                    "version": payload.get("version") if isinstance(payload.get("version"), str) else "",
                    "description": payload.get("description") if isinstance(payload.get("description"), str) else "",
                    "entrypoint_path": str(entrypoint) if entrypoint else "",
                    "registry_path": str(registry_path) if registry_path else "",
                    "entrypoint_declared": has_entrypoint,
                    "registry_declared": has_registry,
                    "entrypoint_exists": bool(entrypoint and entrypoint.is_file()),
                    "registry_exists": bool(registry_path and registry_path.is_file()),
                    "load_mode": load_mode,
                    "registry_entries": registry_entries,
                    "runtime_contributions_unknown": runtime_unknown,
                    "errors": errors,
                    "warnings": warnings,
                    "discrepancies": discrepancies,
                }
            )
    return candidates


def plugin_roots_from_env() -> list[Path]:
    env_value = os.environ.get(PLUGINS_ENV, "")
    roots: list[Path] = []
    if env_value:
        for raw in env_value.split(os.pathsep):
            candidate = Path(raw).expanduser()
            if raw.strip():
                roots.append(candidate)
    roots.append(DEFAULT_PLUGIN_ROOT)
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def discover_plugin_manifests(plugin_roots: Iterable[Path] | None = None) -> list[PluginManifest]:
    roots = list(plugin_roots) if plugin_roots is not None else plugin_roots_from_env()
    manifests: list[PluginManifest] = []
    for root in roots:
        if not root.exists():
            continue
        for plugin_root in sorted(path for path in root.iterdir() if path.is_dir()):
            manifest_path = plugin_root / "plugin.json"
            if not manifest_path.exists():
                continue
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            api_version = payload.get("dwarf_api_version")
            if api_version != DWARF_API_VERSION:
                raise ValueError(
                    f"plugin {payload.get('plugin_id', plugin_root.name)!r} targets {api_version!r}, "
                    f"expected {DWARF_API_VERSION!r}"
                )
            entrypoint = payload.get("entrypoint")
            registry = payload.get("registry")
            manifests.append(
                PluginManifest(
                    plugin_id=payload.get("plugin_id", plugin_root.name),
                    plugin_root=plugin_root,
                    dwarf_api_version=api_version,
                    entrypoint=(plugin_root / entrypoint).resolve() if entrypoint else None,
                    registry_path=(plugin_root / registry).resolve() if registry else None,
                )
            )
    return manifests


def _load_module(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load plugin module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_params_schema(entry: dict, plugin_root: Path) -> dict:
    params_schema = entry.get("params_schema")
    if params_schema and not Path(params_schema).is_absolute():
        entry = dict(entry)
        entry["params_schema"] = str((plugin_root / params_schema).resolve())
    return entry


def load_plugin_registry_entries(manifest: PluginManifest) -> dict[str, dict]:
    """Read one plugin's declarative registry without importing its code.

    Dashboard inventory views use this boundary so listing a plugin can never
    execute its entrypoint. Runtime registry assembly still calls
    :func:`load_plugin_entries`, which deliberately imports entrypoint-backed
    registrations.
    """

    if manifest.registry_path is None:
        return {}
    payload = json.loads(manifest.registry_path.read_text(encoding="utf-8"))
    primitives = payload.get("primitives") or {}
    if not isinstance(primitives, dict):
        raise ValueError(
            f"plugin {manifest.plugin_id!r} registry 'primitives' must be an object"
        )
    return {
        name: _normalize_params_schema(dict(entry), manifest.plugin_root)
        for name, entry in primitives.items()
    }


def load_plugin_entries(manifests: Iterable[PluginManifest]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for manifest in manifests:
        static_entries = load_plugin_registry_entries(manifest)
        collisions = sorted(set(merged).intersection(static_entries))
        if collisions:
            raise ValueError(
                f"plugin {manifest.plugin_id!r} registry collides with existing "
                f"primitive(s): {collisions}"
            )
        merged.update(static_entries)
        if manifest.entrypoint:
            module = _load_module(f"dwarf_plugin_{manifest.plugin_id}", manifest.entrypoint)
            register = getattr(module, "register", None)
            if register is None:
                raise ValueError(f"plugin {manifest.plugin_id!r} missing register(registry) entrypoint")
            before = deepcopy(merged)
            register(merged)
            overwritten = sorted(
                name for name, entry in before.items() if merged.get(name) != entry
            )
            removed = sorted(set(before).difference(merged))
            if overwritten or removed:
                changed = sorted(set(overwritten + removed))
                raise ValueError(
                    f"plugin {manifest.plugin_id!r} entrypoint overwrote existing "
                    f"primitive(s): {changed}"
                )
            for name, entry in list(merged.items()):
                if isinstance(entry, dict):
                    merged[name] = _normalize_params_schema(entry, manifest.plugin_root)
    return merged
