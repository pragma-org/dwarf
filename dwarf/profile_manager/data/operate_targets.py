"""Fuzz-target catalog for /operate/targets.

Walks dwarf/targets/manifests/*.yaml and reads raw JSON defensively to
extract the catalog fields (id, implementation, decoder_type, invariants,
upstream_commit). Mirrors slice-7 / slice-9 / slice-10 per-file enrichment;
malformed JSON or missing required field -> manifest silently dropped.

The _target_url helper is the single source of truth for target URLs.
Future cross-link consumers (e.g., a target column on /operate/runs) must
import the helper rather than inline /operate/targets# strings.

Curated columns (visible):
    id, implementation, decoder_type, invariants, upstream_commit

Deliberately omitted (anti-creep rails enforced by tests):
    binary           — verbose path noise (drill into manifest)
    input_format     — uniform "stdin_bytes" across all current manifests
    language         — derivable from implementation today (YAGNI)
    expected_outcomes — three-key dict; cluttered at catalog density;
                        future per-target page handles it
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from profile_manager.data.catalog_definitions import (
    list_definitions,
    packaged_definition_path,
    validate_target_manifest,
)
from profile_manager.primitives import _resolve_runtime_path
from profile_manager.run_plan import RunPlanError, RunPlanRequest, resolve_run_plan
from profile_manager.scenario import compare_preflight


def manifests_dir() -> Path:
    """Writable manifests catalog. Honors ADA2_DWARF_MANIFESTS_DIR (seeded from
    the baked read-only manifests at install) so targets can be registered from
    the dashboard; falls back to the baked source tree."""
    env = os.environ.get("ADA2_DWARF_MANIFESTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "targets" / "manifests"


def _target_url(target_id: str) -> str:
    """Single source of truth for target detail URLs.

    Returns the target's complete definition detail route.
    """
    return f"/operate/targets/{target_id}"


_TARGET_REFERENCE_KEYS = {"target_id", "reference_target_id", "candidate_target_id"}


def scenario_references_target(value: Any, target_id: str) -> bool:
    """Match only declared target-reference fields in a scenario definition."""
    if isinstance(value, dict):
        return any(
            (key in _TARGET_REFERENCE_KEYS and item == target_id)
            or scenario_references_target(item, target_id)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(scenario_references_target(item, target_id) for item in value)
    return False


def _runtime_manifest_path(target_id: str, source_path: Path, source: bytes) -> Path:
    """Use the immutable packaged path when the writable seed is byte-identical."""
    packaged = packaged_definition_path("targets", target_id)
    try:
        if packaged.is_file() and packaged.read_bytes() == source:
            return packaged
    except OSError:
        pass
    return source_path


def target_runtime_entrypoints(
    target_id: str, *, source_path: Path, source: bytes
) -> dict[str, Any]:
    """Describe executable and canonical scenario readiness without running work."""
    try:
        runtime_manifest = _runtime_manifest_path(target_id, source_path, source)
        manifest = json.loads(runtime_manifest.read_text(encoding="utf-8"))
        validate_target_manifest(manifest)
        if manifest["id"] != target_id:
            raise ValueError(f"target manifest id mismatch in {runtime_manifest}")
        binary = _resolve_runtime_path(manifest["binary"])
        if not binary.is_file():
            raise FileNotFoundError(f"target binary not found: {binary}")
        if not os.access(binary, os.X_OK):
            raise PermissionError(f"target binary is not executable: {binary}")
    except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
        executable_ready = False
        executable_reason = f"Target executable is unavailable: {exc}"
    else:
        executable_ready = True
        executable_reason = "The exact target executable is present and executable."

    scenarios: list[dict[str, Any]] = []
    for record in list_definitions("scenarios"):
        if not scenario_references_target(record.data, target_id):
            continue
        try:
            preflight = compare_preflight(record.path)
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            preflight = {
                "ready": False,
                "code": "scenario-preflight-invalid",
                "message": f"Scenario preflight is unavailable: {exc}",
            }
        try:
            plan = resolve_run_plan(RunPlanRequest(scenario_id=record.definition_id))
            availability = plan["availability"]
        except (KeyError, OSError, RunPlanError, TypeError, ValueError, RuntimeError) as exc:
            availability = {
                "state": "unavailable",
                "reason": str(exc),
            }
        state = str(availability.get("state") or "unavailable")
        ready = bool(
            executable_ready
            and preflight.get("ready") is True
            and state in {"ready", "pullable"}
        )
        reason = (
            str(availability.get("reason") or "Scenario availability is unknown.")
            if preflight.get("ready") is True
            else str(preflight.get("message") or "Scenario preflight is unavailable.")
        )
        if preflight.get("ready") is True and not executable_ready:
            reason = executable_reason
        scenarios.append(
            {
                "id": record.definition_id,
                "title": record.data.get("title") or record.definition_id,
                "start_url": f"/run?scenario={record.definition_id}",
                "available": ready,
                "state": state if executable_ready and preflight.get("ready") else "unavailable",
                "reason": reason,
            }
        )
    scenarios.sort(key=lambda item: item["id"])

    ready_count = sum(1 for item in scenarios if item["available"])
    if executable_ready and ready_count:
        state = "ready"
        reason = (
            "Executable target and one scenario are ready."
            if ready_count == 1
            else f"Executable target and {ready_count} scenarios are ready."
        )
    elif not executable_ready:
        state, reason = "unavailable", executable_reason
    elif scenarios:
        state, reason = "unavailable", "No referencing scenario is currently runnable."
    else:
        state, reason = "unavailable", "No shipped scenario references this target."
    return {
        "state": state,
        "reason": reason,
        "executable_ready": executable_ready,
        "scenarios": scenarios,
    }


def _enrich_target(manifest: dict[str, Any]) -> dict[str, Any]:
    """Translate a raw manifest dict into a render-ready row.

    Required fields (id, implementation) raise KeyError if missing — the
    walker catches that and drops the manifest from the listing.
    """
    target_id = manifest["id"]
    implementation = manifest["implementation"]
    invariants = list(manifest.get("invariants") or [])
    return {
        "id": target_id,
        "url": _target_url(target_id),
        "edit_url": f"/operate/targets/{target_id}/edit",
        "download_url": f"/api/catalog/targets/{target_id}/download",
        "implementation": implementation,
        "decoder_type": manifest.get("decoder_type"),
        "invariants": invariants,
        "invariants_count": len(invariants),
        "upstream_commit": manifest.get("upstream_commit", "unknown"),
    }


DEFAULT_MANIFESTS_DIR = manifests_dir()

_M2_TARGET_MARKERS = ("-cbor-decode-", "-mini-protocol-decode-")


def _is_m2_delivery_target_id(target_id: str) -> bool:
    """True for target manifests that are directly in the June M2 serdes scope."""
    return any(marker in target_id for marker in _M2_TARGET_MARKERS)


def operate_target_rows(*, manifests_dir: Path | None = None) -> list[dict[str, Any]]:
    """Walk *.yaml under manifests_dir; return enriched rows.

    Defensive: each manifest is parsed in its own try/except. Malformed
    JSON, OSError, missing required key (id, implementation) -> manifest
    silently dropped. Order is alphabetical by filename (= alphabetical
    by id, since id matches filename by convention).
    """
    base = Path(manifests_dir) if manifests_dir is not None else DEFAULT_MANIFESTS_DIR
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.yaml")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            row = _enrich_target(data)
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            continue
        out.append(row)
    return out


def operate_m2_target_rows(*, manifests_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return only target manifests that match the June M2 delivery scope.

    The full framework catalog remains available via operate_target_rows().
    The delivery dashboard uses this narrower list so it does not present
    future ledger, cargo-fuzz, or template harnesses as M2 deliverables.
    """
    return [
        row
        for row in operate_target_rows(manifests_dir=manifests_dir)
        if _is_m2_delivery_target_id(row["id"])
    ]


def implementation_pill_inventory(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter-pill set: all (default-active), amaru, cardano-node.

    Pills with zero matching rows still render. Other implementation
    values match only the all pill.
    """
    amaru_count = sum(1 for r in rows if r.get("implementation") == "amaru")
    cn_count = sum(1 for r in rows if r.get("implementation") == "cardano-node")
    return [
        {"slug": "", "label": "all", "count": len(rows), "active": True},
        {"slug": "amaru", "label": "amaru", "count": amaru_count, "active": False},
        {"slug": "cardano-node", "label": "cardano-node", "count": cn_count, "active": False},
    ]
