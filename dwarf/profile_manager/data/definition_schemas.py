"""Schema and registry descriptors used by DWARF's definition builders."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


_DWARF_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY_PATH = _DWARF_ROOT / "primitives" / "registry.json"
_SCENARIO_SCHEMA_PATH = _DWARF_ROOT / "spec" / "v1" / "schema.json"


_KNOWN_OPTION_HELP = {
    "library": "Run a library or executable harness directly; no node network is started.",
    "single-node": "Run against one real node process.",
    "devnet": "Run against a real multi-node network provisioned or attached by DWARF.",
    "candidate": "Retain evidence as a candidate that still requires triage and promotion.",
    "regression": "Exercise a previously classified behavior to detect recurrence.",
    "finding-validation": "Collect evidence intended to confirm or reject a security finding.",
    "risk-support": "Collect evidence supporting a threat-model or risk-register decision.",
    "amaru": "Use the Rust Amaru implementation.",
    "cardano-node": "Use the Haskell cardano-node implementation.",
    "mixed": "Use interoperating Amaru and cardano-node processes.",
    "true": "Enable this behavior.",
    "false": "Disable this behavior.",
}


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _safe_schema_path(relative_path: str) -> Path:
    root = _DWARF_ROOT.resolve()
    candidate = (_DWARF_ROOT / relative_path).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise ValueError(f"primitive schema is outside DWARF or missing: {relative_path}")
    return candidate


def _schema_type(details: dict[str, Any]) -> str:
    raw = details.get("type", "value")
    if isinstance(raw, list):
        return " or ".join(str(item) for item in raw)
    return str(raw)


def _fallback_help(name: str, details: dict[str, Any], *, context: str) -> str:
    label = name.replace("_", " ")
    help_text = f"{label.capitalize()} used by {context}. Expected {_schema_type(details)}."
    constraints = []
    if "minimum" in details:
        constraints.append(f"minimum {details['minimum']}")
    if "maximum" in details:
        constraints.append(f"maximum {details['maximum']}")
    if "default" in details:
        constraints.append(f"default {details['default']!r}")
    if constraints:
        help_text += " Constraints: " + ", ".join(constraints) + "."
    return help_text


def _with_ui_help(schema: dict[str, Any], *, context: str) -> dict[str, Any]:
    """Add render-only help without weakening or duplicating validation rules."""
    enriched = dict(schema)
    properties = {}
    for name, raw_details in (schema.get("properties") or {}).items():
        details = dict(raw_details)
        details["x-ui-help"] = details.get("description") or _fallback_help(
            name, details, context=context
        )
        if details.get("enum"):
            configured = details.get("x-ui-option-descriptions") or {}
            details["x-ui-option-descriptions"] = {
                str(value): configured.get(str(value))
                or _KNOWN_OPTION_HELP.get(str(value).lower())
                or f"Use the supported {value!r} value for {name.replace('_', ' ')}."
                for value in details["enum"]
            }
        if details.get("properties"):
            nested = _with_ui_help(details, context=f"the {name.replace('_', ' ')} object")
            details["properties"] = nested["properties"]
        properties[name] = details
    enriched["properties"] = properties
    return enriched


def scenario_editor_descriptor() -> dict[str, Any]:
    """Resolve every registered primitive and its parameter schema.

    The UI receives the registry as data rather than a separately maintained
    list, so additions and removals become visible without editing dashboard
    code. Missing or unsafe schema paths fail closed while rendering.
    """
    registry_document = _read_json(_REGISTRY_PATH)
    registry = registry_document.get("primitives")
    if not isinstance(registry, dict):
        raise ValueError("primitive registry must contain a primitives object")

    primitives: dict[str, dict[str, Any]] = {}
    family_counts: Counter[str] = Counter()
    for name, raw_entry in sorted(registry.items()):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"primitive {name} must be an object")
        schema_path = raw_entry.get("params_schema")
        if not isinstance(schema_path, str):
            raise ValueError(f"primitive {name} has no params_schema")
        params_schema = _read_json(_safe_schema_path(schema_path))
        entry = {
            "family": raw_entry.get("family"),
            "runtimes": list(raw_entry.get("runtimes") or []),
            "supports": list(raw_entry.get("supports") or []),
            "version": raw_entry.get("version", ""),
            "params_schema": _with_ui_help(
                params_schema, context=f"the {name} primitive"
            ),
        }
        if not isinstance(entry["family"], str):
            raise ValueError(f"primitive {name} has no family")
        family_counts[entry["family"]] += 1
        primitives[name] = entry

    return {
        "scenario_schema": _with_ui_help(
            _read_json(_SCENARIO_SCHEMA_PATH), context="the scenario contract"
        ),
        "primitives": primitives,
        "primitive_count": len(primitives),
        "family_counts": dict(sorted(family_counts.items())),
    }
