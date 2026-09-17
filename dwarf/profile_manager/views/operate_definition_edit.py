"""Shared structured/raw editor for allow-listed DWARF definitions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from profile_manager.data.catalog_definitions import (
    CatalogError,
    catalog_label,
    load_definition,
    list_definitions,
)
from profile_manager.templating import render


_SPEC_ROOT = Path(__file__).resolve().parents[2] / "spec" / "v1"
_AUTHORING_HELP = {
    "scenarios": ("/learn/overview#dsl", "Scenario authoring guide"),
    "profiles": (
        "/learn/developer-onboarding#authoring-profiles",
        "Profile authoring guide",
    ),
    "targets": (
        "/learn/developer-onboarding#authoring-targets",
        "Target authoring guide",
    ),
}


def _schema(catalog: str) -> dict[str, Any]:
    filename = {
        "profiles": "profile.schema.json",
        "targets": "target-manifest.schema.json",
        "scenarios": "schema.json",
    }.get(catalog)
    if filename is None:
        raise ValueError(f"editor descriptor is not implemented for {catalog}")
    return json.loads((_SPEC_ROOT / filename).read_text(encoding="utf-8"))


def _field_descriptors(schema: dict[str, Any]) -> list[dict[str, Any]]:
    required = set(schema.get("required") or [])
    fields = []
    for name, details in (schema.get("properties") or {}).items():
        option_help = details.get("x-ui-option-descriptions") or {}
        fields.append(
            {
                "name": name,
                "label": name.replace("_", " ").title(),
                "type": details.get("x-ui-type") or details.get("type", "string"),
                "enum": details.get("enum") or [],
                "enum_options": [
                    {
                        "value": value,
                        "description": option_help.get(str(value), ""),
                    }
                    for value in (details.get("enum") or [])
                ],
                "minimum": details.get("minimum"),
                "description": details.get("description", ""),
                "placeholder": details.get("x-ui-placeholder", ""),
                "required": name in required,
            }
        )
    return fields


def _profile_templates() -> dict[str, dict[str, Any]]:
    from profile_manager.profile_templates import list_templates, render_template_source

    templates: dict[str, dict[str, Any]] = {}
    for name in list_templates():
        source = render_template_source(
            template_name=name,
            profile_name="new-profile",
        )
        data = yaml.safe_load(source)
        if isinstance(data, dict):
            data["label"] = "New profile"
            templates[name] = data
    return templates


def _new_scenario(template: str | None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    options = [
        {"id": record.definition_id, "title": str(record.data.get("title") or record.definition_id)}
        for record in list_definitions("scenarios")
    ]
    data: dict[str, Any] = {
        "spec_version": "v1",
        "id": "new-scenario",
        "title": "New scenario",
        "authors": ["dwarf"],
        "tags": [],
        "target": {"implementation": "amaru", "version": "any"},
        "runtime": "library",
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }
    if template:
        try:
            data = dict(load_definition("scenarios", template).data)
            data["id"] = "new-scenario"
            data["title"] = "New scenario"
        except CatalogError:
            pass
    return data, options


def render_operate_definition_edit(
    catalog: str,
    definition_id: str | None,
    *,
    token: str | None = None,
    template: str | None = None,
) -> str | None:
    """Render a create or edit form without mutating the catalog."""
    create = definition_id is None
    try:
        schema = _schema(catalog)
        templates = _profile_templates() if catalog == "profiles" else {}
        if create:
            selected = template if template in templates else (next(iter(templates), None))
            if catalog == "targets":
                data = {
                    "id": "new-target",
                    "binary": "dwarf/targets/new-target",
                    "input_format": "stdin_bytes",
                    "implementation": "amaru",
                    "language": "rust",
                    "upstream_commit": "",
                    "decoder_type": "CBOR codec",
                    "invariants": ["no panic on bounded input"],
                }
            elif catalog == "scenarios":
                data, scenario_templates = _new_scenario(template)
                selected = template or ""
            else:
                data = dict(templates.get(selected, {}))
            source = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        else:
            record = load_definition(catalog, definition_id or "")
            data = record.data
            source = record.source.decode("utf-8")
    except (CatalogError, OSError, ValueError, yaml.YAMLError):
        return None

    label = catalog_label(catalog)
    help_href, help_aria_label = _AUTHORING_HELP[catalog]
    if catalog == "scenarios":
        from profile_manager.data.definition_schemas import scenario_editor_descriptor

        descriptor = scenario_editor_descriptor()
        profile_options = [
            {"id": record.definition_id, "label": str(record.data.get("label") or record.definition_id)}
            for record in list_definitions("profiles")
        ]
        return render(
            "operate/scenario_editor.j2",
            page_title=f"{'New' if create else 'Edit'} scenario",
            density="reading",
            layout="wide",
            active="operate",
            active_sub=catalog,
            catalog=catalog,
            catalog_label=label,
            create=create,
            definition_id=definition_id or "",
            initial_data_json=json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"),
            initial_source=source,
            descriptor_json=json.dumps(descriptor, ensure_ascii=False).replace("<", "\\u003c"),
            primitive_count=descriptor["primitive_count"],
            family_counts=descriptor["family_counts"],
            scenario_templates=scenario_templates if create else [],
            selected_template=selected if create else "",
            profile_options=profile_options,
            help_href=help_href,
            help_aria_label=help_aria_label,
            token=token or "dwarf",
        )
    return render(
        "operate/definition_editor.j2",
        page_title=f"{'New' if create else 'Edit'} {label.lower()}",
        density="reading",
        layout="wide",
        active="operate",
        active_sub=catalog,
        catalog=catalog,
        catalog_label=label,
        create=create,
        definition_id=definition_id or "",
        fields=_field_descriptors(schema),
        initial_data_json=json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"),
        initial_source=source,
        templates=templates,
        templates_json=json.dumps(templates, ensure_ascii=False).replace("<", "\\u003c"),
        selected_template=template or "",
        help_href=help_href,
        help_aria_label=help_aria_label,
        token=token or "dwarf",
    )
