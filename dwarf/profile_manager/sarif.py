"""Shared SARIF generation for automatic run finalization and explicit export."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema


DWARF_ROOT = Path(__file__).resolve().parents[1]
SARIF_SCHEMA_URI = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
SARIF_SCHEMA_PATH = DWARF_ROOT / "spec" / "sarif-schema-2.1.0.json"
DWARF_VERSION = "0.1.0"
DWARF_INFORMATION_URI = "https://github.com/pragma-org/dwarf"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _tool_component(name: str) -> dict[str, Any]:
    return {"name": name, "version": DWARF_VERSION, "informationUri": DWARF_INFORMATION_URI}


def _base_run(*, tool_name: str, target_run_id: str, bundle_dir: Path, manifest: dict) -> dict:
    scenario = manifest.get("scenario") or {}
    return {
        "tool": {"driver": _tool_component(tool_name)},
        "automationDetails": {"id": target_run_id},
        "results": [],
        "properties": {
            "dwarf.target_run_id": target_run_id,
            "dwarf.bundle_dir": str(bundle_dir),
            "dwarf.scenario_id": scenario.get("id"),
            "dwarf.exit_status": manifest.get("exit_status"),
            "dwarf.assertion_summary": manifest.get("assertion_summary") or {},
        },
    }


def _artifact_location(bundle_dir: Path, relpath: str) -> dict:
    return {"uri": str(bundle_dir / relpath)}


def _assertion_run(*, target_run_id: str, bundle_dir: Path, manifest: dict, assertions: list) -> dict:
    run = _base_run(tool_name="DWARF scenario assertions", target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest)
    for index, assertion in enumerate(assertions):
        if assertion.get("result") != "fail":
            continue
        primitive = str(assertion.get("primitive") or "unknown")
        note = str(assertion.get("note") or "DWARF assertion failed")
        run["results"].append({
            "ruleId": f"dwarf.assertion.{primitive}",
            "level": "error",
            "message": {"text": f"{primitive}: {note}"},
            "locations": [{"physicalLocation": {"artifactLocation": _artifact_location(bundle_dir, "assertions.json")}}],
            "properties": {
                "dwarf.assertion_index": index,
                "dwarf.primitive": primitive,
                "dwarf.params": assertion.get("params") or {},
                "dwarf.evaluated_value": assertion.get("evaluated_value"),
                "dwarf.data_points_used": assertion.get("data_points_used"),
            },
        })
    return run


def _bundle_diff_run(*, target_run_id: str, bundle_dir: Path, manifest: dict, body: dict) -> dict:
    run = _base_run(tool_name="DWARF bundle diff", target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest)
    for comparison in body.get("comparisons") or []:
        verdict = comparison.get("verdict")
        if verdict == "match":
            continue
        relpath = comparison.get("relpath", "<unknown>")
        run["results"].append({
            "ruleId": f"dwarf.bundle-diff.{verdict}",
            "level": "error" if verdict == "diff" else "warning",
            "message": {"text": f"Bundle comparison {verdict} for {relpath}"},
            "locations": [{"physicalLocation": {"artifactLocation": _artifact_location(bundle_dir, relpath)}}],
            "properties": {
                "dwarf.relpath": relpath,
                "dwarf.verdict": verdict,
                "dwarf.left_sha256": comparison.get("left_sha256"),
                "dwarf.right_sha256": comparison.get("right_sha256"),
            },
        })
    return run


def _bundle_replay_run(*, target_run_id: str, bundle_dir: Path, manifest: dict, body: dict) -> dict:
    run = _base_run(tool_name="DWARF bundle replay", target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest)
    if body.get("comparison_verdict") != "diff":
        return run
    for comparison in body.get("comparisons") or []:
        if comparison.get("verdict") == "match":
            continue
        relpath = comparison.get("relpath", "<unknown>")
        run["results"].append({
            "ruleId": f"dwarf.bundle-replay.{comparison.get('verdict', 'diff')}",
            "level": "error",
            "message": {"text": f"Replay diverged for {relpath}"},
            "locations": [{"physicalLocation": {"artifactLocation": _artifact_location(bundle_dir, relpath)}}],
            "properties": {
                "dwarf.relpath": relpath,
                "dwarf.verdict": comparison.get("verdict"),
                "dwarf.target_run_id": body.get("target_run_id"),
                "dwarf.replay_run_id": body.get("replay_run_id"),
            },
        })
    return run


def build_sarif_log(*, bundle_dir: Path, target_run_id: str) -> dict:
    manifest = _load_json(bundle_dir / "manifest.json", {}) or {}
    assertions = _load_json(bundle_dir / "assertions.json", []) or []
    runs = [_assertion_run(target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest, assertions=assertions)]
    diff_body = _load_json(bundle_dir / "outputs/bundle-diff/diff.json")
    if diff_body:
        runs.append(_bundle_diff_run(target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest, body=diff_body))
    replay_body = _load_json(bundle_dir / "outputs/replay/result.json")
    if replay_body:
        runs.append(_bundle_replay_run(target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest, body=replay_body))
    return {"$schema": SARIF_SCHEMA_URI, "version": "2.1.0", "runs": runs}


def validate_sarif(sarif_log: dict, *, schema_path: Path = SARIF_SCHEMA_PATH) -> tuple[bool, str | None]:
    try:
        jsonschema.validate(instance=sarif_log, schema=_load_json(Path(schema_path), {}))
    except jsonschema.ValidationError as exc:
        return False, exc.message
    return True, None


def run_sarif_export(*, runs_dir: Path, output_dir: Path, target_run_id: str, schema_path: Path = SARIF_SCHEMA_PATH, generation: str = "explicit-primitive") -> dict:
    bundle_dir = Path(runs_dir) / target_run_id
    sarif_log = build_sarif_log(bundle_dir=bundle_dir, target_run_id=target_run_id)
    schema_valid, validation_error = validate_sarif(sarif_log, schema_path=Path(schema_path))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sarif_path = output_dir / "dwarf-export.sarif"
    sarif_path.write_text(json.dumps(sarif_log, indent=2) + "\n", encoding="utf-8")
    mapped = ["assertions"]
    if (bundle_dir / "outputs/bundle-diff/diff.json").is_file():
        mapped.append("bundle-diff")
    if (bundle_dir / "outputs/replay/result.json").is_file():
        mapped.append("bundle-replay")
    payload = {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "exported_at_utc": utc_timestamp(),
        "generation": generation,
        "schema_path": str(schema_path),
        "schema_valid": schema_valid,
        "validation_error": validation_error,
        "sarif_result_count": sum(len(run.get("results") or []) for run in sarif_log["runs"]),
        "sarif_run_count": len(sarif_log["runs"]),
        "mapped_evidence_types": mapped,
        "sarif_relpath": "outputs/sarif-export/dwarf-export.sarif",
        "result_relpath": "outputs/sarif-export/result.json",
    }
    (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
