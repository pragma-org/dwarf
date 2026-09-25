"""Basic-view facts for catalog definition detail cards.

Turns a definition's raw fields into typed rows the Evidence Deck can render
as pills and flags instead of JSON dumps. Presentation only; the Advanced view
keeps the original summary fields and the full source.
"""
from __future__ import annotations

from typing import Any

_PREFERRED = {
    "scenarios": ("runtime", "target", "profile", "duration", "evidence_intent"),
    "targets": ("implementation", "language", "decoder_type", "input_format", "upstream_commit"),
    "profiles": ("node_type", "node_count", "amaru_node_count", "network_magic", "peer_sharing",
                 "version_policy", "cardano_version", "amaru_version", "compatibility_pair"),
    "measurements": ("collection_mode", "compatibility", "default_enabled", "overhead_class", "required_capabilities"),
    "measurement-profiles": ("implementation", "target_modes", "measurements"),
}

_LABELS = {
    "node_type": "Implementation", "implementation": "Implementation", "node_count": "Cardano nodes",
    "amaru_node_count": "Amaru nodes", "network_magic": "Network", "version_policy": "Versions",
    "cardano_version": "cardano-node", "amaru_version": "Amaru", "compatibility_pair": "Compatibility pair",
    "collection_mode": "Collection", "overhead_class": "Overhead", "required_capabilities": "Requires",
    "target_modes": "Modes", "measurements": "Taps", "evidence_intent": "Evidence intent",
    "decoder_type": "Decoder", "input_format": "Input format", "upstream_commit": "Upstream",
}

# Boolean fields shown as a single on/off flag pill instead of a row.
_FLAGS = {"default_enabled": "default", "peer_sharing": "peer sharing"}

# Scalar fields whose value doubles as a coloured pill (data-tag styling in deck.css).
_PILL_SCALARS = {"implementation", "node_type", "runtime", "collection_mode", "overhead_class", "decoder_type"}


def _version_pill(impl: str, version: str) -> str:
    return ("amaru " if impl.startswith("amaru") else "node ") + str(version)


def _network(value: Any) -> str:
    magic = str(value)
    return {"1": "preprod", "2": "preview", "764824073": "mainnet"}.get(magic, f"devnet {magic}")


def _pill_value(key: str, value: Any) -> str:
    text = str(value).replace("-", " ") if key == "collection_mode" else str(value)
    return f"{text} overhead" if key == "overhead_class" else text


def definition_facts(catalog: str, data: dict) -> dict:
    """Return {'flags': [(label, on)], 'rows': [{'label', 'kind', 'value'|'pills'|'versions'}], 'accent': impl}."""
    flags: list[tuple[str, bool]] = []
    rows: list[dict] = []
    accent = ""
    for key in _PREFERRED.get(catalog, ()):
        if key not in data or data[key] in (None, "", [], {}):
            continue
        value = data[key]
        label = _LABELS.get(key, key.replace("_", " ").capitalize())
        if key in _FLAGS:
            flags.append((_FLAGS[key], bool(value)))
            continue
        if key == "compatibility" and isinstance(value, dict):
            impl = str(value.get("implementation") or "")
            if impl:
                accent = accent or impl
                rows.append({"label": "Implementation", "kind": "pills", "pills": [impl]})
            modes = value.get("target_modes") or []
            if modes:
                rows.append({"label": "Modes", "kind": "pills", "pills": [str(m) for m in modes]})
            versions = []
            for item in value.get("versions") or []:
                if isinstance(item, dict) and item.get("version"):
                    versions.append({"pill": _version_pill(impl, item["version"]),
                                     "detail": f"source revision {item.get('source_revision') or 'unknown'}"})
            if versions:
                rows.append({"label": "Versions", "kind": "versions", "versions": versions})
            continue
        if key in ("node_type", "implementation") and isinstance(value, str):
            accent = accent or value
        if key == "network_magic":
            rows.append({"label": label, "kind": "pills", "pills": [_network(value)]})
        elif key in ("cardano_version", "amaru_version"):
            rows.append({"label": "Version", "kind": "versions",
                         "versions": [{"pill": _version_pill("amaru" if key == "amaru_version" else "node", value), "detail": ""}]})
        elif isinstance(value, list):
            items = [v if not isinstance(v, dict) else (v.get("id") or v.get("name") or ", ".join(f"{a}: {b}" for a, b in v.items())) for v in value]
            if key == "measurements":
                # Tap ids repeat the implementation the card already shows.
                items = [str(i).split("-", 1)[1].replace("-", " ") if str(i).startswith(("amaru-", "cardano-")) else i for i in items]
            rows.append({"label": label, "kind": "pills", "pills": [str(i) for i in items]})
        elif isinstance(value, dict):
            rows.append({"label": label, "kind": "text", "value": ", ".join(f"{a}: {b}" for a, b in value.items())})
        elif isinstance(value, bool):
            flags.append((label.lower(), value))
        elif key in _PILL_SCALARS:
            rows.append({"label": label, "kind": "pills", "pills": [_pill_value(key, value)]})
        else:
            rows.append({"label": label, "kind": "text", "value": str(value)})
    return {"flags": flags, "rows": rows, "accent": accent if accent in ("amaru", "cardano-node", "mixed") else ""}
