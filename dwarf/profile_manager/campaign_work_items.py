"""Derive immutable, bounded runner work from a frozen campaign manifest."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _finish(item: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(item)
    result["work_item_digest"] = _digest(result)
    result["shard"] = f"{result['kind']}-{result['work_item_digest'].split(':', 1)[1][:16]}"
    return result


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
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
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
            key = (cell["topology_id"], cell["node_id"], cell["scenario_id"], mode)
            groups.setdefault(key, []).append(cell)
    items = []
    for topology_id, node_id, scenario_id, mode in sorted(groups):
        cells = groups[(topology_id, node_id, scenario_id, mode)]
        measurement_ids = sorted(cell["measurement_id"] for cell in cells)
        selector = {
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
