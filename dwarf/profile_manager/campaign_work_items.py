"""Derive immutable, bounded runner work from a frozen campaign manifest."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PHASES = ("amaru-solo", "cardano-solo", "mixed")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _finish(item: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(item)
    result["work_item_digest"] = _digest(result)
    result["shard"] = f"{result['kind']}-{result['work_item_digest'].split(':', 1)[1][:16]}"
    return result


def _phase(implementations: set[str]) -> str:
    if implementations == {"amaru"}:
        return "amaru-solo"
    if implementations == {"cardano-node"}:
        return "cardano-solo"
    if implementations == {"amaru", "cardano-node"}:
        return "mixed"
    raise ValueError("work item has an unsupported implementation scope")


def _topology_phases(manifest: dict[str, Any]) -> dict[str, str]:
    result = {}
    for topology in manifest.get("topologies", []):
        topology_id = topology.get("id")
        implementations = {
            node.get("identity", {}).get("implementation")
            for node in topology.get("nodes", [])
        }
        if not isinstance(topology_id, str) or not topology_id:
            raise ValueError("work item topology has no identity")
        result[topology_id] = _phase(implementations)
    return result


def _measurement_phase(
    manifest: dict[str, Any], scenario_id: str, implementation: str
) -> str:
    scenarios = manifest.get("definitions", {}).get("scenarios", {})
    frozen = scenarios.get(scenario_id, {})
    document = frozen.get("document", {}) if isinstance(frozen, dict) else {}
    substrate_nodes = document.get("substrate", {}).get("nodes", [])
    implementations = {
        node.get("impl")
        for node in substrate_nodes
        if isinstance(node, dict) and node.get("impl")
    }
    if implementations:
        return _phase(implementations)
    profile_id = document.get("profile")
    profiles = manifest.get("definitions", {}).get("profiles", {})
    profile = profiles.get(profile_id, {}).get("document", {}) if profile_id else {}
    if isinstance(profile, dict) and profile:
        scoped = set()
        if int(profile.get("amaru_count", profile.get("amaru_node_count", 0)) or 0):
            scoped.add("amaru")
        if int(profile.get("haskell_count", profile.get("node_count", 0)) or 0):
            scoped.add("cardano-node")
        if scoped:
            return _phase(scoped)
    return _phase({implementation})


def _base(manifest: dict[str, Any], *, role: str, kind: str, item_id: str) -> dict[str, Any]:
    campaign_digest = manifest.get("campaign_digest")
    if not _DIGEST.fullmatch(str(campaign_digest or "")):
        raise ValueError("work items require a frozen campaign digest")
    if role not in {"primary", "stock"}:
        raise ValueError("work item manifest role is invalid")
    return {
        "schema_version": "v1",
        "kind": kind,
        "item_id": item_id,
        "manifest_role": role,
        "campaign_digest": campaign_digest,
    }


def calibration_work_items(
    primary_manifest: dict[str, Any], stock_manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return independent calibration legs required by a patched-first plan."""

    candidate_modes = {
        node["identity"].get("mode", "stock")
        for topology in primary_manifest.get("topologies", [])
        for node in topology.get("nodes", [])
        if node.get("identity", {}).get("kind") == "candidate"
    }
    if not candidate_modes or not candidate_modes <= {"stock", "patched"}:
        raise ValueError("calibration work requires one supported frozen candidate mode")
    rows = []
    if "patched" in candidate_modes:
        rows.append(
            _finish(
                {
                    **_base(
                        primary_manifest,
                        role="primary",
                        kind="calibration",
                        item_id="calibration:patched",
                    ),
                    "mode": "patched",
                }
            )
        )
    rows.append(
        _finish(
            {
                **_base(
                    stock_manifest,
                    role="stock",
                    kind="calibration",
                    item_id="calibration:stock",
                ),
                "mode": "stock",
            }
        )
    )
    return sorted(rows, key=lambda row: row["item_id"])


def _measurement_groups(manifest: dict[str, Any], *, role: str):
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    blockers = []
    for cell in manifest.get("required_tap_cells", []):
        runtime = cell.get("runtime", {})
        for mode in sorted(cell.get("target_modes", [])):
            if runtime.get("status") != "implemented":
                blockers.append(
                    {
                        "topology_id": cell["topology_id"],
                        "node_id": cell["node_id"],
                        "measurement_id": cell["measurement_id"],
                        "mode": mode,
                        "reason": str(runtime.get("reason") or "measurement runtime is not implemented"),
                    }
                )
                continue
            key = (
                cell["implementation"],
                cell["topology_id"],
                cell["node_id"],
                cell["scenario_id"],
                mode,
            )
            groups.setdefault(key, []).append(cell)
    items = []
    for implementation, topology_id, node_id, scenario_id, mode in sorted(groups):
        cells = groups[(implementation, topology_id, node_id, scenario_id, mode)]
        measurement_ids = sorted(cell["measurement_id"] for cell in cells)
        selector = {
            "phase": _measurement_phase(manifest, scenario_id, implementation),
            "implementation": implementation,
            "topology_id": topology_id,
            "node_id": node_id,
            "scenario_id": scenario_id,
            "mode": mode,
        }
        items.append(
            _finish(
                {
                    **_base(
                        manifest,
                        role=role,
                        kind="measurement",
                        item_id="measurement:" + _digest(selector).split(":", 1)[1],
                    ),
                    **selector,
                    "measurement_ids": measurement_ids,
                }
            )
        )
    return items, sorted(
        blockers,
        key=lambda row: (
            row["topology_id"], row["node_id"], row["measurement_id"], row["mode"]
        ),
    )


def execution_work_items(
    manifest: dict[str, Any],
    *,
    stock_manifest: dict[str, Any] | None = None,
    stock_replay_required: bool = False,
) -> dict[str, Any]:
    """Create one item per security entry and executable measurement recipe."""

    items = []
    primary_phases = _topology_phases(manifest)
    for entry in sorted(manifest.get("entries", []), key=lambda row: row["id"]):
        items.append(
            _finish(
                {
                    **_base(
                        manifest,
                        role="primary",
                        kind="security",
                        item_id="security:" + entry["id"],
                    ),
                    "phase": primary_phases[entry["topology_id"]],
                    "entry_id": entry["id"],
                    "topology_id": entry["topology_id"],
                    "scenario_id": entry["contract"]["scenario_id"],
                }
            )
        )
    measurements, blockers = _measurement_groups(manifest, role="primary")
    items.extend(measurements)
    if stock_replay_required:
        if stock_manifest is None:
            raise ValueError("stock replay work requires a frozen stock manifest")
        stock_phases = _topology_phases(stock_manifest)
        for entry in sorted(stock_manifest.get("entries", []), key=lambda row: row["id"]):
            items.append(
                _finish(
                    {
                        **_base(
                            stock_manifest,
                            role="stock",
                            kind="stock-replay",
                            item_id="stock-replay:" + entry["id"],
                        ),
                        "phase": stock_phases[entry["topology_id"]],
                        "entry_id": entry["id"],
                        "topology_id": entry["topology_id"],
                        "scenario_id": entry["contract"]["scenario_id"],
                    }
                )
            )
    items.sort(key=lambda row: row["item_id"])
    return {
        "schema_version": "v1",
        "kind": "campaign-work-item-inventory",
        "campaign_digest": manifest["campaign_digest"],
        "items": items,
        "blockers": blockers,
        "inventory_digest": _digest({"items": items, "blockers": blockers}),
    }


def phase_work_items(inventory: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Partition one frozen inventory in the required execution order."""

    result = {phase: [] for phase in _PHASES}
    for item in inventory.get("items", []):
        phase = item.get("phase")
        if phase not in result:
            raise ValueError("work item has an invalid execution phase")
        result[phase].append(deepcopy(item))
    return result


def validate_work_item(item: dict[str, Any], allowed: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate an item by self-digest and exact membership in a frozen inventory."""

    if not isinstance(item, dict):
        raise ValueError("work item must be an object")
    digest = item.get("work_item_digest")
    body = {key: value for key, value in item.items() if key not in {"work_item_digest", "shard"}}
    if not _DIGEST.fullmatch(str(digest or "")) or digest != _digest(body):
        raise ValueError("work item digest mismatch")
    matches = [candidate for candidate in allowed if candidate.get("work_item_digest") == digest]
    if len(matches) != 1 or matches[0] != item:
        raise ValueError("work item is not present in the frozen inventory")
    return deepcopy(item)
