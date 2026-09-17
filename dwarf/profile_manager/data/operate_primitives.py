"""Read-only primitive inventory for Operate and Learn.

The runtime registry remains authoritative.  Built-ins are validated through
``profile_manager.primitives.load_registry``.  Plugin inventory reads only the
declarative registry files discovered by ``plugin_loader``; it never imports a
plugin entrypoint merely to render a page.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from profile_manager import plugin_loader, primitives
from profile_manager.data.asset_catalog import (
    DEFAULT_REGISTRY,
    AssetCatalog,
    CatalogItem,
    is_safe_asset_id,
)


DWARF_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = DWARF_ROOT / "primitives" / "registry.json"


def _default_scenarios_dir() -> Path:
    configured = os.environ.get("ADA2_DWARF_SCENARIOS_DIR")
    return Path(configured) if configured else DWARF_ROOT / "scenarios"


def _collect_primitive_names(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        primitive = value.get("primitive")
        if isinstance(primitive, str) and primitive:
            found.add(primitive)
        for child in value.values():
            _collect_primitive_names(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_primitive_names(child, found)


def scenario_primitive_references(scenarios_dir: Path | None = None) -> dict[str, list[str]]:
    """Map primitive name to scenario ids by inspecting source definitions.

    Scenario files are JSON-compatible YAML in the shipped catalog.  Invalid
    or non-object definitions are ignored here and remain the scenario
    catalog's responsibility; this reverse index never executes a scenario.
    """

    root = Path(scenarios_dir) if scenarios_dir is not None else _default_scenarios_dir()
    references: dict[str, set[str]] = {}
    if not root.is_dir():
        return {}
    for path in sorted(root.glob("*.yaml")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        scenario_id = payload.get("id")
        if not isinstance(scenario_id, str) or not scenario_id:
            scenario_id = path.stem
        names: set[str] = set()
        _collect_primitive_names(payload, names)
        for name in names:
            references.setdefault(name, set()).add(scenario_id)
    return {name: sorted(ids) for name, ids in sorted(references.items())}


def _schema_state(path: Path | None) -> tuple[str, str, str | None]:
    if path is None:
        return "missing-schema", "", "no params_schema is declared"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return "missing-schema", "", str(exc)
    try:
        payload = json.loads(source)
    except json.JSONDecodeError as exc:
        return "invalid-schema", source, str(exc)
    if not isinstance(payload, dict):
        return "invalid-schema", source, "schema root must be an object"
    return "schema-backed", source, None


def _builtin_executor_state(module_name: str, class_name: str) -> tuple[str, str | None]:
    try:
        module = importlib.import_module(module_name)
        executor = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        return "executor-missing", str(exc)
    if not isinstance(executor, type) or not issubclass(executor, primitives.Primitive):
        return "executor-invalid", f"{module_name}.{class_name} is not a Primitive subclass"
    return "executor-present", None


def _display_path(path: Path) -> str:
    try:
        return f"dwarf/{path.resolve().relative_to(DWARF_ROOT.resolve()).as_posix()}"
    except ValueError:
        return str(path)


def _record(
    *,
    name: str,
    raw_entry: dict[str, Any],
    entry: primitives.RegistryEntry,
    source_type: str,
    source_path: Path,
    plugin_id: str | None,
    references: dict[str, list[str]],
) -> dict[str, Any]:
    schema_path = Path(entry.params_schema) if entry.params_schema else None
    if schema_path is not None and not schema_path.is_absolute():
        schema_path = DWARF_ROOT / schema_path
    schema_status, schema_source, schema_error = _schema_state(schema_path)
    if source_type == "built-in":
        executor_status, executor_error = _builtin_executor_state(
            entry.module, entry.class_name
        )
    else:
        executor_status, executor_error = "declared-unloaded", None
    errors = [error for error in (schema_error, executor_error) if error]
    status = (
        "catalog-valid"
        if schema_status == "schema-backed" and executor_status == "executor-present"
        else "declared-unloaded"
        if schema_status == "schema-backed" and executor_status == "declared-unloaded"
        else "invalid"
    )
    scenario_ids = references.get(name, [])
    registry_source = json.dumps({name: raw_entry}, indent=2, sort_keys=True) + "\n"
    source_label = (
        f"dwarf/primitives/registry.json#primitives.{name}"
        if source_type == "built-in"
        else f"{_display_path(source_path)}#primitives.{name}"
    )
    relationships = [
        {
            "catalog": "scenarios",
            "id": scenario_id,
            "label": f"Scenario {scenario_id}",
            "url": f"/operate/scenarios/{scenario_id}",
            "relation": "used-by",
            "source_path": f"dwarf/scenarios/{scenario_id}.yaml#primitive={name}",
            "resolved": True,
        }
        for scenario_id in scenario_ids
    ]
    return {
        "id": name,
        "label": name,
        "family": entry.family,
        "module": entry.module,
        "class_name": entry.class_name,
        "version": entry.version,
        "supports": list(entry.supports),
        "runtimes": list(entry.runtimes),
        "params_schema": raw_entry.get("params_schema") or "",
        "schema_path": _display_path(schema_path) if schema_path else "",
        "schema_status": schema_status,
        "schema_source": schema_source,
        "executor_status": executor_status,
        "source_type": source_type,
        "plugin_id": plugin_id,
        "source_path": source_label,
        "registry_source": registry_source,
        "referencing_scenarios": scenario_ids,
        "relationships": relationships,
        "referencing_scenario_count": len(scenario_ids),
        "errors": errors,
        "status": status,
        "summary": (
            f"{entry.family.title()} primitive for "
            f"{', '.join(entry.supports) or 'no declared target'}; "
            f"{', '.join(entry.runtimes) or 'no declared runtime'}."
        ),
    }


def primitive_catalog_payload(
    *,
    registry_path: Path | None = None,
    scenarios_dir: Path | None = None,
    plugin_roots: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Return reconciled built-in and static-plugin rows plus diagnostics."""

    registry_path = Path(registry_path or DEFAULT_REGISTRY_PATH)
    references = scenario_primitive_references(scenarios_dir)
    raw_payload = json.loads(registry_path.read_text(encoding="utf-8"))
    raw_builtins = raw_payload.get("primitives") or {}
    builtins = primitives.load_registry(registry_path, plugin_roots=[])
    rows = [
        _record(
            name=name,
            raw_entry=dict(raw_builtins[name]),
            entry=entry,
            source_type="built-in",
            source_path=registry_path,
            plugin_id=None,
            references=references,
        )
        for name, entry in sorted(builtins.items())
    ]
    claimed = set(builtins)
    errors: list[str] = []

    candidates = plugin_loader.inspect_plugin_candidates(plugin_roots=plugin_roots)
    for candidate in candidates:
        plugin_id = candidate["plugin_id"]
        candidate_errors = candidate["errors"]
        errors.extend(f"Plugin {plugin_id}: {error}" for error in candidate_errors)
        if candidate_errors:
            continue
        static_entries = candidate["registry_entries"]
        if candidate["runtime_contributions_unknown"]:
            errors.append(
                f"Plugin {plugin_id}: runtime entrypoint contributions are not listed "
                "because inventory does not execute plugin code"
            )
        for name, raw_entry in sorted(static_entries.items()):
            if not is_safe_asset_id(name):
                errors.append(
                    f"Plugin {plugin_id}: unsafe primitive name: {name!r}"
                )
                continue
            if name in claimed:
                errors.append(
                    f"Plugin {plugin_id}: primitive name collision: {name}"
                )
                continue
            try:
                parsed = primitives._entries_from_map({name: raw_entry})[name]
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"Plugin {plugin_id} primitive {name}: {exc}")
                continue
            rows.append(
                _record(
                    name=name,
                    raw_entry=dict(raw_entry),
                    entry=parsed,
                    source_type="plugin",
                    source_path=Path(
                        candidate["registry_path"] or candidate["plugin_root"]
                    ),
                    plugin_id=plugin_id,
                    references=references,
                )
            )
            claimed.add(name)

    rows.sort(key=lambda row: row["id"])
    family_counts = {
        family: sum(row["family"] == family for row in rows)
        for family in sorted(primitives.FAMILIES)
    }
    return {
        "rows": rows,
        "errors": errors,
        "family_counts": family_counts,
        "runtimes": sorted(primitives.RUNTIMES),
        "supports": sorted(
            {support for row in rows for support in row["supports"]}
        ),
    }


def primitive_catalog_rows(**kwargs) -> list[dict[str, Any]]:
    return primitive_catalog_payload(**kwargs)["rows"]


def primitive_detail(name: str, **kwargs) -> dict[str, Any] | None:
    return next(
        (row for row in primitive_catalog_rows(**kwargs) if row["id"] == name),
        None,
    )


def _catalog_items() -> tuple[CatalogItem, ...]:
    items: list[CatalogItem] = []
    for row in primitive_catalog_rows():
        relationships = tuple(row["relationships"])
        facets = {
            "family": (row["family"],),
            "runtime": tuple(row["runtimes"]),
            "support": tuple(row["supports"]),
            "source_type": (row["source_type"],),
            "version": (row["version"],),
        }
        items.append(
            CatalogItem(
                catalog="primitives",
                item_id=row["id"],
                label=row["label"],
                source_path=row["source_path"],
                status=row["status"],
                facets=facets,
                summary=row["summary"],
                raw_text=row["registry_source"],
                export_path=f"dwarf/primitives/catalog/{row['id']}.json",
                relationships=relationships,
                content_type="application/json; charset=utf-8",
            )
        )
    return tuple(items)


PRIMITIVE_CATALOG = AssetCatalog(
    slug="primitives",
    label="Primitives",
    singular_label="Primitive",
    description=(
        "Typed setup, load, probe, assertion, fault, and teardown operations "
        "available to scenario definitions."
    ),
    active_sub="primitives",
    load_items=_catalog_items,
)

if DEFAULT_REGISTRY.get(PRIMITIVE_CATALOG.slug) is None:
    DEFAULT_REGISTRY.register(PRIMITIVE_CATALOG)
