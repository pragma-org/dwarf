"""Read-only catalog of shipped profile scaffolds."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml

from profile_manager.data.asset_catalog import DEFAULT_REGISTRY, AssetCatalog, CatalogItem
from profile_manager.data.catalog_definitions import (
    CatalogError,
    validate_profile_definition,
)
from profile_manager.profile_templates import (
    TEMPLATES_DIR,
    list_templates,
    render_template_source,
    template_path,
)


_SUBSTITUTION = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def _as_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _node_mix(data: dict[str, Any]) -> dict[str, int]:
    node_type = data.get("node_type")
    haskell = _as_count(data.get("haskell_count"))
    amaru = _as_count(data.get("amaru_count"))
    if "haskell_count" not in data and node_type in {"cardano-node", "mixed"}:
        haskell = _as_count(data.get("node_count"))
    if "amaru_count" not in data:
        amaru = _as_count(data.get("amaru_node_count"))
    return {"cardano-node": haskell, "amaru": amaru}


def _node_type(data: dict[str, Any], mix: dict[str, int]) -> str:
    if isinstance(data.get("node_type"), str):
        return data["node_type"]
    if mix["cardano-node"] and mix["amaru"]:
        return "mixed"
    if mix["amaru"]:
        return "amaru"
    return "cardano-node"


def _assumptions(data: dict[str, Any]) -> list[str]:
    values = [
        f"network magic {data.get('network_magic')}",
        "peer sharing enabled" if data.get("peer_sharing") else "peer sharing disabled",
    ]
    if data.get("shared_genesis") is True:
        values.append("shared generated genesis")
    if data.get("topology_pattern"):
        values.append(f"topology {data['topology_pattern']}")
    if data.get("amaru_network"):
        values.append(f"Amaru public network {data['amaru_network']}")
    if data.get("upstream_peer_address"):
        values.append(f"external upstream {data['upstream_peer_address']}")
    return values


def _template_row(name: str, *, templates_dir: Path) -> dict[str, Any]:
    path = template_path(name, templates_dir=templates_dir)
    source = path.read_text(encoding="utf-8")
    substitutions = sorted(set(_SUBSTITUTION.findall(source)))
    preview_name = f"preview-{name}"
    rendered_source = ""
    rendered_data: dict[str, Any] = {}
    validation_error: str | None = None
    try:
        rendered_source = render_template_source(
            template_name=name,
            profile_name=preview_name,
            templates_dir=templates_dir,
        )
        loaded = yaml.safe_load(rendered_source)
        if not isinstance(loaded, dict):
            raise ValueError("rendered template root must be an object")
        rendered_data = loaded
        validate_profile_definition(rendered_data)
    except (CatalogError, OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        validation_error = str(exc)
    mix = _node_mix(rendered_data)
    node_type = "invalid" if validation_error else _node_type(rendered_data, mix)
    return {
        "id": name,
        "label": name,
        "status": "invalid" if validation_error else "valid",
        "source_path": f"dwarf/profiles/templates/{name}.yaml",
        "raw_source": source,
        "required_substitutions": substitutions,
        "rendered_source": rendered_source,
        "rendered_data": rendered_data,
        "validation_error": validation_error,
        "node_mix": mix,
        "node_type": node_type,
        "node_total": sum(mix.values()),
        "network_magic": rendered_data.get("network_magic"),
        "peer_sharing": rendered_data.get("peer_sharing"),
        "topology_pattern": rendered_data.get("topology_pattern") or "explicit profile fields",
        "assumptions": [] if validation_error else _assumptions(rendered_data),
        "use_url": f"/operate/profiles/new?template={name}",
        # A scaffold is not a deployed profile. No reciprocal relationship is
        # declared until another persisted source explicitly names it.
        "relationships": [],
        "summary": (
            f"{node_type} profile scaffold · {sum(mix.values())} node"
            f"{'' if sum(mix.values()) == 1 else 's'} · network magic "
            f"{rendered_data.get('network_magic', 'invalid')}"
        ),
    }


def profile_template_catalog_rows(
    *, templates_dir: Path | None = None
) -> list[dict[str, Any]]:
    root = Path(templates_dir or TEMPLATES_DIR)
    return [_template_row(name, templates_dir=root) for name in list_templates(templates_dir=root)]


def profile_template_detail(
    name: str, *, templates_dir: Path | None = None
) -> dict[str, Any] | None:
    return next(
        (row for row in profile_template_catalog_rows(templates_dir=templates_dir) if row["id"] == name),
        None,
    )


def _catalog_items() -> tuple[CatalogItem, ...]:
    return tuple(
        CatalogItem(
            catalog="profile-templates",
            item_id=row["id"],
            label=row["label"],
            source_path=row["source_path"],
            status=row["status"],
            facets={
                "node_type": (row["node_type"],),
                "topology": (row["topology_pattern"],),
                "peer_sharing": (str(row["peer_sharing"]).lower(),),
            },
            summary=row["summary"],
            raw_text=row["raw_source"],
            export_path=row["source_path"],
            relationships=tuple(row["relationships"]),
            content_type="application/yaml; charset=utf-8",
        )
        for row in profile_template_catalog_rows()
    )


PROFILE_TEMPLATE_CATALOG = AssetCatalog(
    slug="profile-templates",
    label="Profile templates",
    singular_label="Profile template",
    description="Read-only shipped scaffolds for creating deployment profiles.",
    active_sub="profile-templates",
    load_items=_catalog_items,
)

if DEFAULT_REGISTRY.get(PROFILE_TEMPLATE_CATALOG.slug) is None:
    DEFAULT_REGISTRY.register(PROFILE_TEMPLATE_CATALOG)
