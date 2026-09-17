"""Shared detail view for scenario, target, and profile definitions."""
from __future__ import annotations

import json

from profile_manager.data.catalog_definitions import (
    catalog_label,
    load_definition,
)
from profile_manager.templating import render


def _summary_fields(catalog: str, data: dict) -> list[dict[str, str]]:
    preferred = {
        "scenarios": ("runtime", "target", "profile", "duration", "evidence_intent"),
        "targets": ("implementation", "language", "decoder_type", "input_format", "upstream_commit"),
        "profiles": ("node_type", "node_count", "amaru_node_count", "network_magic", "peer_sharing"),
    }[catalog]
    fields = []
    for key in preferred:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, (dict, list)):
            text = json.dumps(value, sort_keys=True)
        elif isinstance(value, bool):
            text = "yes" if value else "no"
        elif value is None:
            text = "—"
        else:
            text = str(value)
        fields.append({"key": key.replace("_", " "), "value": text})
    return fields


def render_operate_definition(catalog: str, definition_id: str) -> str:
    record = load_definition(catalog, definition_id)
    label = catalog_label(catalog)
    title = record.data.get("title") or record.data.get("label") or definition_id
    return render(
        "operate/definition_detail.j2",
        page_title=f"{label} · {definition_id}",
        density="reading",
        layout="wide",
        active="operate",
        active_sub=catalog,
        catalog=catalog,
        catalog_label=label,
        definition_id=definition_id,
        title=title,
        source_path=str(record.path),
        raw_source=record.source.decode("utf-8", errors="replace"),
        summary_fields=_summary_fields(catalog, record.data),
        catalog_url=f"/operate/{catalog}",
        edit_url=f"/operate/{catalog}/{definition_id}/edit",
        download_url=f"/api/catalog/{catalog}/{definition_id}/download",
    )
