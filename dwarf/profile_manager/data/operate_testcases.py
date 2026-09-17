"""Read-only Operate projections of the canonical testcase lifecycle state."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from profile_manager import testcase_lifecycle
from profile_manager.data.asset_catalog import (
    DEFAULT_REGISTRY,
    AssetCatalog,
    CatalogItem,
    is_safe_asset_id,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _default_state_dir() -> Path:
    configured = os.environ.get("ADA2_DWARF_STATE_DIR", "").strip()
    if configured:
        return Path(configured)
    candidates = (PROJECT_ROOT / "dwarf" / "state", PROJECT_ROOT / "state")
    return next((path for path in candidates if path.exists()), candidates[0])


def _default_runs_dir() -> Path:
    configured = os.environ.get("ADA2_DWARF_RUNS_DIR", "").strip()
    if configured:
        return Path(configured)
    candidates = (PROJECT_ROOT / "dwarf" / "runs", PROJECT_ROOT / "runs")
    return next((path for path in candidates if path.exists()), candidates[0])


def _default_scenarios_dir() -> Path:
    configured = os.environ.get("ADA2_DWARF_SCENARIOS_DIR", "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "dwarf" / "scenarios"


def _roots(
    *, state_dir: Path | None = None, runs_dir: Path | None = None
) -> tuple[Path, Path]:
    return Path(state_dir or _default_state_dir()), Path(runs_dir or _default_runs_dir())


def _contained_artifact(runs_dir: Path, record: dict[str, Any]) -> tuple[str, Path | None]:
    run_id = str(record.get("source_run_id") or "")
    relative = str(record.get("source_artifact_path") or "")
    if not run_id or not relative:
        return "metadata-only", None
    if not is_safe_asset_id(run_id) or "\\" in relative or "\x00" in relative:
        return "unsafe-path", None
    posix = PurePosixPath(relative)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        return "unsafe-path", None
    base = (runs_dir / run_id).resolve()
    candidate = (base / Path(*posix.parts)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return "unsafe-path", None
    if not candidate.is_file():
        return "artifact-missing", candidate
    return "recorded", candidate


def _row_from_source(
    source: dict[str, Any], *, runs_dir: Path, scenarios_dir: Path
) -> dict[str, Any]:
    record = source["record"]
    artifact_status, artifact_path = _contained_artifact(runs_dir, record)
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    case_id = record["case_id"]
    run_id = str(record.get("source_run_id") or "")
    scenario_id = str(metadata.get("scenario_id") or "")
    run_resolved = bool(run_id and is_safe_asset_id(run_id) and (runs_dir / run_id).is_dir())
    scenario_resolved = bool(
        scenario_id
        and is_safe_asset_id(scenario_id)
        and any(
            (scenarios_dir / f"{scenario_id}{suffix}").is_file()
            for suffix in (".yaml", ".yml", ".json")
        )
    )
    relationships = []
    if run_id:
        relationships.append(
            {
                "catalog": "runs",
                "id": run_id,
                "label": f"Run {run_id}",
                "url": f"/operate/runs/{run_id}",
                "relation": "produced-by",
                "source_path": f"{source['path']}#source_run_id",
                "resolved": run_resolved,
            }
        )
    if scenario_id:
        relationships.append(
            {
                "catalog": "scenarios",
                "id": scenario_id,
                "label": f"Scenario {scenario_id}",
                "url": f"/operate/scenarios/{scenario_id}",
                "relation": "generated-by",
                "source_path": f"{source['path']}#metadata.scenario_id",
                "resolved": scenario_resolved,
            }
        )
    retained_files = [
        {
            "label": "Lifecycle record",
            "path": source["path"],
            "download_url": f"/api/assets/testcases/{case_id}/download",
        }
    ]
    if artifact_status == "recorded" and artifact_path is not None:
        retained_files.append(
            {
                "label": "Source artifact",
                "path": str(artifact_path),
                "download_url": f"/api/assets/testcases/{case_id}/artifact",
            }
        )
    return {
        "id": case_id,
        "case_id": case_id,
        "status": artifact_status,
        "diagnostic": (
            "The retained source artifact is missing."
            if artifact_status == "artifact-missing"
            else "The source artifact path is outside its retained run."
            if artifact_status == "unsafe-path"
            else None
        ),
        "bucket_id": record.get("bucket_id") or "unbucketed",
        "classification": record.get("classification") or "unknown",
        "triage_reason": record.get("triage_reason") or "unknown",
        "producer": record.get("producer") or "unknown",
        "target_implementation": record.get("target_implementation") or "unknown",
        "source_run_id": run_id,
        "source_run_resolved": run_resolved,
        "source_run_url": (
            f"/operate/runs/{record['source_run_id']}"
            if run_id else None
        ),
        "scenario_id": scenario_id,
        "scenario_resolved": scenario_resolved,
        "scenario_url": (
            f"/operate/scenarios/{metadata['scenario_id']}"
            if scenario_id else None
        ),
        "source_artifact_path": record.get("source_artifact_path") or "",
        "artifact_available": artifact_status == "recorded",
        "artifact_path": str(artifact_path) if artifact_path is not None else None,
        "sha256": record.get("sha256"),
        "size_bytes": record.get("size_bytes"),
        "replay_state": record.get("replay_state") or "none",
        "compare_state": record.get("compare_state") or "none",
        "minimization_state": record.get("minimization_state") or "none",
        "promotion": record.get("promotion") or {},
        "promotion_state": (record.get("promotion") or {}).get("state") or "unpromoted",
        "metadata": metadata,
        "record": record,
        "raw_source": source["raw_text"],
        "source_path": source["path"],
        "retained_files": retained_files,
        "relationships": relationships,
        "summary": (
            f"{record.get('classification') or 'unknown'} · "
            f"{record.get('target_implementation') or 'unknown'} · "
            f"{record.get('triage_reason') or 'unknown'}"
        ),
    }


def _malformed_row(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": source["case_id"],
        "case_id": source["case_id"],
        "status": "malformed",
        "diagnostic": source["error"],
        "bucket_id": "unbucketed",
        "classification": "malformed",
        "triage_reason": "record-parse-error",
        "producer": "unknown",
        "target_implementation": "unknown",
        "source_run_id": "",
        "source_run_resolved": False,
        "source_run_url": None,
        "scenario_id": "",
        "scenario_resolved": False,
        "scenario_url": None,
        "source_artifact_path": "",
        "artifact_available": False,
        "artifact_path": None,
        "sha256": None,
        "size_bytes": None,
        "replay_state": "unknown",
        "compare_state": "unknown",
        "minimization_state": "unknown",
        "promotion": {},
        "promotion_state": "unknown",
        "metadata": {},
        "record": None,
        "raw_source": source["raw_text"],
        "source_path": source["path"],
        "retained_files": [
            {
                "label": "Malformed lifecycle record",
                "path": source["path"],
                "download_url": f"/api/assets/testcases/{source['case_id']}/download",
            }
        ],
        "relationships": [],
        "summary": f"Malformed lifecycle record · {source['error']}",
    }


def testcase_catalog_rows(
    *, state_dir: Path | None = None, runs_dir: Path | None = None,
    scenarios_dir: Path | None = None,
) -> list[dict[str, Any]]:
    state_root, run_root = _roots(state_dir=state_dir, runs_dir=runs_dir)
    snapshot = testcase_lifecycle.read_testcase_state(state_dir=state_root)
    rows = [
        _row_from_source(
            source,
            runs_dir=run_root,
            scenarios_dir=Path(scenarios_dir or _default_scenarios_dir()),
        )
        for source in snapshot["record_sources"]
    ]
    rows.extend(_malformed_row(source) for source in snapshot["malformed_records"])
    return sorted(rows, key=lambda row: row["id"])


def testcase_detail(
    case_id: str, *, state_dir: Path | None = None, runs_dir: Path | None = None,
    scenarios_dir: Path | None = None,
) -> dict[str, Any] | None:
    if not is_safe_asset_id(case_id):
        return None
    row = next(
        (
            item
            for item in testcase_catalog_rows(
                state_dir=state_dir, runs_dir=runs_dir, scenarios_dir=scenarios_dir
            )
            if item["id"] == case_id
        ),
        None,
    )
    if row is None:
        return None
    record = row.get("record") or {}
    targets = [str(value) for value in record.get("replay_targets", []) if value]
    commands = [
        f"./dwarf/cardano-profile testcase replay {case_id} --target {target}"
        for target in targets
    ]
    if targets and row["artifact_available"]:
        commands.append(
            f"./dwarf/cardano-profile testcase minimize {case_id} "
            f"--target {targets[0]} --manifests-dir <target-manifests-dir>"
        )
    if len(targets) > 1:
        commands.append(f"./dwarf/cardano-profile testcase compare {case_id}")
    if row["bucket_id"] != "unbucketed":
        commands.append(
            "./dwarf/cardano-profile testcase promote bucket "
            f"{row['bucket_id']} --state <candidate|validated|finding> "
            "--summary <summary> --source <evidence-source>"
        )
    return {
        **row,
        "replay_targets": targets,
        "replay_results": list(record.get("replay_results", [])),
        "minimization_results": list(record.get("minimization_results", [])),
        "compare_result": record.get("compare_result") or {},
        "promotion_history": list(record.get("promotion_history", [])),
        "bucket_signature": record.get("bucket_signature") or {},
        "cli_commands": commands,
    }


def testcase_bucket_rows(
    *, state_dir: Path | None = None, runs_dir: Path | None = None,
    scenarios_dir: Path | None = None,
) -> list[dict[str, Any]]:
    state_root, run_root = _roots(state_dir=state_dir, runs_dir=runs_dir)
    snapshot = testcase_lifecycle.read_testcase_state(state_dir=state_root)
    cases = {
        row["id"]: row
        for row in testcase_catalog_rows(
            state_dir=state_root,
            runs_dir=run_root,
            scenarios_dir=scenarios_dir,
        )
    }
    rows: list[dict[str, Any]] = []
    for bucket in snapshot["bucket_rows"]:
        signature = bucket.get("bucket_signature") or {}
        members = [cases[case_id] for case_id in bucket.get("case_ids", []) if case_id in cases]
        relationships = [
            {
                "catalog": "testcases",
                "id": member["id"],
                "label": f"Testcase {member['id']}",
                "url": f"/operate/testcases/{member['id']}",
                "relation": "contains",
                "source_path": "derived from state/testcases/*.json#bucket_id",
                "resolved": True,
            }
            for member in members
        ]
        rows.append(
            {
                **bucket,
                "id": bucket["bucket_id"],
                "classification": signature.get("classification") or "unknown",
                "triage_reason": signature.get("triage_reason") or "unknown",
                "target_implementation": signature.get("target_implementation") or "unknown",
                "promotion_state": (bucket.get("promotion") or {}).get("state") or "unpromoted",
                "pending_replay_count": sum(row["replay_state"] == "pending" for row in members),
                "pending_compare_count": sum(row["compare_state"] == "pending" for row in members),
                "members": members,
                "relationships": relationships,
                "summary": (
                    f"{len(members)} case{'' if len(members) == 1 else 's'} · "
                    f"{signature.get('classification') or 'unknown'} · "
                    f"{signature.get('triage_reason') or 'unknown'}"
                ),
            }
        )
    return sorted(rows, key=lambda row: row["id"])


def testcase_bucket_detail(
    bucket_id: str, *, state_dir: Path | None = None, runs_dir: Path | None = None,
    scenarios_dir: Path | None = None,
) -> dict[str, Any] | None:
    if not is_safe_asset_id(bucket_id):
        return None
    return next(
        (
            row
            for row in testcase_bucket_rows(
                state_dir=state_dir, runs_dir=runs_dir, scenarios_dir=scenarios_dir
            )
            if row["id"] == bucket_id
        ),
        None,
    )


def testcase_artifact(
    case_id: str, *, state_dir: Path | None = None, runs_dir: Path | None = None
) -> tuple[str, Path | None]:
    detail = testcase_detail(case_id, state_dir=state_dir, runs_dir=runs_dir)
    if detail is None or not detail.get("record"):
        return "not-found", None
    _state_root, run_root = _roots(state_dir=state_dir, runs_dir=runs_dir)
    return _contained_artifact(run_root, detail["record"])


def _testcase_items() -> tuple[CatalogItem, ...]:
    return tuple(
        CatalogItem(
            catalog="testcases",
            item_id=row["id"],
            label=row["id"],
            source_path=row["source_path"],
            status=row["status"],
            facets={
                "classification": (row["classification"],),
                "producer": (row["producer"],),
                "target": (row["target_implementation"],),
                "bucket": (row["bucket_id"],),
            },
            summary=row["summary"],
            raw_text=row["raw_source"],
            export_path=f"dwarf/state/testcases/{row['id']}.json",
            relationships=tuple(row["relationships"]),
            content_type="application/json; charset=utf-8",
        )
        for row in testcase_catalog_rows()
        if is_safe_asset_id(row["id"])
    )


def _bucket_items() -> tuple[CatalogItem, ...]:
    items = []
    for row in testcase_bucket_rows():
        portable = {
            "bucket_id": row["bucket_id"],
            "bucket_signature": row["bucket_signature"],
            "case_ids": row["case_ids"],
            "case_count": row["case_count"],
            "promotion": row.get("promotion"),
            "pending_replay_count": row["pending_replay_count"],
            "pending_compare_count": row["pending_compare_count"],
        }
        items.append(CatalogItem(
            catalog="testcase-buckets",
            item_id=row["id"],
            label=row["id"],
            source_path="derived from state/testcases/*.json",
            status=row["promotion_state"],
            facets={
                "classification": (row["classification"],),
                "target": (row["target_implementation"],),
                "promotion": (row["promotion_state"],),
            },
            summary=row["summary"],
            raw_text=json.dumps(portable, indent=2, sort_keys=True) + "\n",
            export_path=f"dwarf/state/testcases/buckets/{row['id']}.json",
            relationships=tuple(row["relationships"]),
            content_type="application/json; charset=utf-8",
        ))
    return tuple(items)


TESTCASE_CATALOG = AssetCatalog(
    slug="testcases",
    label="Testcases",
    singular_label="Testcase",
    description="Read-only records retained by the testcase lifecycle.",
    active_sub="testcases",
    load_items=_testcase_items,
)
TESTCASE_BUCKET_CATALOG = AssetCatalog(
    slug="testcase-buckets",
    label="Testcase buckets",
    singular_label="Testcase bucket",
    description="Lifecycle-equivalent groupings of recorded testcases.",
    active_sub="testcase-buckets",
    load_items=_bucket_items,
)

for _catalog in (TESTCASE_CATALOG, TESTCASE_BUCKET_CATALOG):
    if DEFAULT_REGISTRY.get(_catalog.slug) is None:
        DEFAULT_REGISTRY.register(_catalog)
