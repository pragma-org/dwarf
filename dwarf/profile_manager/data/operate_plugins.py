"""Non-executing plugin inventory and primitive reconciliation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from profile_manager import plugin_loader, primitives
from profile_manager.data.asset_catalog import (
    ExportSource,
    deterministic_export_archive,
    is_safe_asset_id,
)


DWARF_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUILTIN_REGISTRY = DWARF_ROOT / "primitives" / "registry.json"
TRUST_WARNING = (
    "Plugins execute with the same privileges as DWARF and are not sandboxed. "
    "Inventory reads manifests and declarative registries only; it does not execute entrypoints."
)


def _builtins(path: Path) -> tuple[set[str], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload.get("primitives") or {}
        if not isinstance(raw, dict):
            raise ValueError("built-in registry 'primitives' must be an object")
        return set(raw), []
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return set(), [f"Built-in registry unavailable: {exc}"]


def _catalog_ids(candidates: list[dict[str, Any]]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        plugin_id = candidate["plugin_id"]
        counts[plugin_id] = counts.get(plugin_id, 0) + 1
    result: dict[str, str] = {}
    for candidate in candidates:
        plugin_id = candidate["plugin_id"]
        if is_safe_asset_id(plugin_id) and counts[plugin_id] == 1:
            catalog_id = plugin_id
        else:
            stem = plugin_id if is_safe_asset_id(plugin_id) else candidate["directory_name"]
            if not is_safe_asset_id(stem):
                stem = "plugin"
            suffix = hashlib.sha256(candidate["plugin_root"].encode("utf-8")).hexdigest()[:8]
            catalog_id = f"{stem}--{suffix}"
        result[candidate["plugin_root"]] = catalog_id
    return result


def _reconcile_primitive(
    name: Any,
    raw_entry: Any,
    *,
    claimed: dict[str, str],
    plugin_id: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(name, str) or not is_safe_asset_id(name):
        errors.append(f"unsafe primitive identifier: {name!r}")
        return {
            "id": str(name), "url": None, "status": "invalid", "family": "",
            "module": "", "class_name": "", "errors": errors,
        }
    if not isinstance(raw_entry, dict):
        errors.append("primitive registry entry must be an object")
        return {
            "id": name, "url": f"/operate/primitives/{name}", "status": "invalid",
            "family": "", "module": "", "class_name": "", "errors": errors,
        }
    try:
        parsed = primitives._entries_from_map({name: raw_entry})[name]
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
        return {
            "id": name, "url": f"/operate/primitives/{name}", "status": "invalid",
            "family": raw_entry.get("family", ""),
            "module": raw_entry.get("module", ""),
            "class_name": raw_entry.get("class", ""), "errors": errors,
        }
    collision_source = claimed.get(name)
    if collision_source is not None:
        errors.append(f"primitive name collides with {collision_source}")
        status = "collision"
    else:
        claimed[name] = f"plugin {plugin_id}"
        status = "declared"
    return {
        "id": name, "url": f"/operate/primitives/{name}", "status": status,
        "family": parsed.family, "module": parsed.module,
        "class_name": parsed.class_name, "errors": errors,
    }


def plugin_catalog_payload(
    *,
    plugin_roots: Iterable[Path] | None = None,
    builtin_registry_path: Path | None = None,
) -> dict[str, Any]:
    """Return every plugin candidate and its statically knowable primitives."""

    roots = (
        [Path(root).expanduser().resolve() for root in plugin_roots]
        if plugin_roots is not None
        else plugin_loader.plugin_roots_from_env()
    )
    builtin_names, global_errors = _builtins(
        Path(builtin_registry_path or DEFAULT_BUILTIN_REGISTRY)
    )
    candidates = plugin_loader.inspect_plugin_candidates(roots)
    catalog_ids = _catalog_ids(candidates)
    plugin_id_counts: dict[str, int] = {}
    for candidate in candidates:
        plugin_id_counts[candidate["plugin_id"]] = (
            plugin_id_counts.get(candidate["plugin_id"], 0) + 1
        )
    claimed = {name: "the built-in registry" for name in builtin_names}
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        row["catalog_id"] = catalog_ids[row["plugin_root"]]
        if plugin_id_counts[row["plugin_id"]] > 1:
            row["discrepancies"].append(
                f"duplicate plugin_id {row['plugin_id']!r} appears in multiple roots"
            )
        reconciled = [
            _reconcile_primitive(
                name, raw_entry, claimed=claimed, plugin_id=row["plugin_id"]
            )
            for name, raw_entry in sorted(
                row.pop("registry_entries").items(), key=lambda item: str(item[0])
            )
        ]
        row["primitives"] = reconciled
        row["relationships"] = [
            {
                "catalog": "primitives",
                "id": item["id"],
                "label": f"Primitive {item['id']}",
                "url": item["url"],
                "relation": "declares",
                "source_path": (
                    f"{row['registry_path']}#primitives.{item['id']}"
                    if row.get("registry_path")
                    else f"{row['manifest_path']}#registry"
                ),
                "resolved": item["status"] in {"declared", "collision"},
            }
            for item in reconciled
        ]
        for item, relationship in zip(reconciled, row["relationships"]):
            item["resolved"] = relationship["resolved"]
        row["static_primitive_count"] = len(reconciled)
        row["collisions"] = [
            item["id"] for item in reconciled if item["status"] == "collision"
        ]
        for item in reconciled:
            if item["status"] == "invalid":
                row["errors"].extend(
                    f"primitive {item['id']}: {error}" for error in item["errors"]
                )
            elif item["status"] == "collision":
                row["discrepancies"].extend(item["errors"])
        if row["errors"] or row["collisions"]:
            row["status"] = "invalid"
        elif row["discrepancies"] or row["runtime_contributions_unknown"]:
            row["status"] = "attention"
        else:
            row["status"] = "declared"
        row["trust_warning"] = TRUST_WARNING
        rows.append(row)

    rows.sort(key=lambda row: (row["plugin_id"], row["plugin_root"]))
    return {
        "plugins": rows,
        "plugin_roots": [str(root) for root in roots],
        "default_plugin_root": str(plugin_loader.DEFAULT_PLUGIN_ROOT),
        "plugins_env": plugin_loader.PLUGINS_ENV,
        "expected_api_version": plugin_loader.DWARF_API_VERSION,
        "total_primitives": sum(row["static_primitive_count"] for row in rows),
        "invalid_count": sum(row["status"] == "invalid" for row in rows),
        "runtime_unknown_count": sum(row["runtime_contributions_unknown"] for row in rows),
        "global_errors": global_errors,
        "trust_warning": TRUST_WARNING,
    }


def plugin_detail(catalog_id: str, **kwargs: Any) -> dict[str, Any] | None:
    return next(
        (row for row in plugin_catalog_payload(**kwargs)["plugins"] if row["catalog_id"] == catalog_id),
        None,
    )


def _plugin_export_sources(row: dict[str, Any]) -> list[ExportSource]:
    """Return only allow-listed, regular plugin sources contained by its root."""

    root = Path(row["plugin_root"]).resolve()
    declared: list[tuple[str, str]] = [("plugin.json", "plugin.json")]
    manifest = row.get("manifest_source") or {}
    if isinstance(manifest, dict):
        for field in ("registry", "entrypoint"):
            value = manifest.get(field)
            if isinstance(value, str) and value:
                declared.append((field, value))
    sources: list[ExportSource] = []
    seen: set[Path] = set()
    for _field, relative in declared:
        raw = Path(relative)
        if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
            continue
        unresolved = root / raw
        if unresolved.is_symlink():
            continue
        try:
            source = unresolved.resolve()
            source.relative_to(root)
        except (OSError, ValueError):
            continue
        if not source.is_file() or source in seen:
            continue
        seen.add(source)
        export_path = f"dwarf/plugins/{row['catalog_id']}/{raw.as_posix()}"
        sources.append(
            ExportSource(
                object_id=row["catalog_id"],
                source_path=f"plugins/{row['directory_name']}/{raw.as_posix()}",
                export_path=export_path,
                body=source.read_bytes(),
            )
        )
    return sources


def deterministic_plugin_archive(
    *, catalog_id: str | None = None, **kwargs: Any
) -> bytes:
    rows = plugin_catalog_payload(**kwargs)["plugins"]
    if catalog_id is not None:
        rows = [row for row in rows if row["catalog_id"] == catalog_id]
    return deterministic_export_archive(
        "plugins",
        [source for row in rows for source in _plugin_export_sources(row)],
    )


def _attachment(filename: str) -> dict[str, str]:
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


def dispatch_plugin_api_request(path: str, **kwargs: Any):
    parts = urlsplit(path).path.strip("/").split("/")
    if parts == ["api", "plugins", "export"]:
        return (
            200,
            "application/gzip",
            deterministic_plugin_archive(**kwargs),
            _attachment("dwarf-plugins.tar.gz"),
        )
    if len(parts) == 4 and parts[:2] == ["api", "plugins"] and parts[3] == "export":
        catalog_id = unquote(parts[2])
        if not is_safe_asset_id(catalog_id):
            return 400, "text/plain; charset=utf-8", b"invalid plugin id\n"
        if plugin_detail(catalog_id, **kwargs) is None:
            return 404, "text/plain; charset=utf-8", b"not found\n"
        return (
            200,
            "application/gzip",
            deterministic_plugin_archive(catalog_id=catalog_id, **kwargs),
            _attachment(f"{catalog_id}.tar.gz"),
        )
    return None


def operate_plugins_payload() -> dict[str, Any]:
    """Compatibility alias for the original catalog view."""

    return plugin_catalog_payload()
