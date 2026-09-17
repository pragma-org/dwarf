"""Read-only, source-backed catalog for legacy Milestone-1 package records."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from profile_manager.data.asset_catalog import (
    ExportSource,
    deterministic_export_archive,
    is_safe_asset_id,
)
from profile_manager.evidence_packages import (
    risk_work_package_schema,
    risk_work_package_validation_errors,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_NOISE = frozenset({".DS_Store", "__pycache__", ".pytest_cache"})
_SAFE_INDEX = re.compile(r"^[0-9]+$")


def _package_root() -> Path:
    configured = os.environ.get("ADA2_DWARF_RISK_PACKAGES_DIR", "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "dwarf" / "evidence-packages"


def _evidence_root() -> Path:
    configured = os.environ.get("ADA2_DWARF_RISK_EVIDENCE_ROOT", "").strip()
    return Path(configured) if configured else PROJECT_ROOT


def _profiles_dir() -> Path:
    configured = os.environ.get("ADA2_DWARF_PROFILES_DIR", "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "dwarf" / "profiles"


def _source_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        return []
    return [
        path
        for path in sorted(root.glob("package-*.yaml"))
        if path.is_file()
        and not path.is_symlink()
        and not path.name.startswith("._")
        and path.name not in _NOISE
    ]


def _read_yaml_json(path: Path) -> tuple[Any, str, list[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, "", [f"source is not readable UTF-8: {exc}"]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, raw, [f"source is malformed YAML/JSON: {exc}"]
    if not isinstance(data, dict):
        return data, raw, ["root: expected an object"]
    return data, raw, risk_work_package_validation_errors(data)


def _scenario_candidate_map(scenarios_dir: Path) -> dict[str, list[str]]:
    references: dict[str, list[str]] = {}
    if not scenarios_dir.is_dir() or scenarios_dir.is_symlink():
        return references
    for path in sorted((*scenarios_dir.glob("*.yaml"), *scenarios_dir.glob("*.yml"), *scenarios_dir.glob("*.json"))):
        if not path.is_file() or path.is_symlink() or path.name.startswith("._"):
            continue
        try:
            body = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if not isinstance(body, dict):
            continue
        scenario_id = body.get("id")
        trace = body.get("m1_trace")
        candidate_ids = trace.get("risk_candidate_ids", []) if isinstance(trace, dict) else []
        if not is_safe_asset_id(scenario_id) or not isinstance(candidate_ids, list):
            continue
        for candidate_id in candidate_ids:
            if isinstance(candidate_id, str):
                references.setdefault(candidate_id, []).append(scenario_id)
    return {candidate_id: sorted(set(ids)) for candidate_id, ids in references.items()}


def _scenario_candidate_map_from_catalog() -> dict[str, list[str]]:
    """Reuse the existing mtime-aware scenario catalog for live dashboard reads."""

    from profile_manager.data.scenarios import _list_scenarios_for_compare

    references: dict[str, list[str]] = {}
    for row in _list_scenarios_for_compare():
        trace = row.get("m1_trace") or {}
        for candidate_id in trace.get("risk_candidate_ids", []):
            if isinstance(candidate_id, str):
                references.setdefault(candidate_id, []).append(row["id"])
    return {candidate_id: sorted(set(ids)) for candidate_id, ids in references.items()}


def _available_profile_ids(profiles_dir: Path) -> set[str]:
    available: set[str] = set()
    if not profiles_dir.is_dir() or profiles_dir.is_symlink():
        return available
    for source in sorted(profiles_dir.glob("*/profile.yaml")):
        if not source.is_file() or source.is_symlink():
            continue
        try:
            body = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(body, dict) and is_safe_asset_id(body.get("id")):
            available.add(body["id"])
    return available


def _evidence_record(root: Path, package_id: str, index: int, value: Any) -> dict[str, Any]:
    record = {
        "index": index,
        "path": value if isinstance(value, str) else str(value),
        "status": "unsafe",
        "download_url": None,
        "resolved_path": None,
    }
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return record
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return record
    try:
        resolved_root = root.resolve()
        unresolved = root / Path(*relative.parts)
        if unresolved.is_symlink():
            return record
        candidate = unresolved.resolve()
        candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return record
    if candidate.is_symlink():
        return record
    record["resolved_path"] = str(candidate)
    if candidate.is_file():
        record["status"] = "available-file"
        record["download_url"] = f"/api/risk-packages/{package_id}/evidence/{index}/download"
    elif candidate.is_dir():
        record["status"] = "available-directory"
    else:
        record["status"] = "missing"
    return record


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def risk_package_catalog_rows(
    *,
    package_root: Path | None = None,
    evidence_root: Path | None = None,
    scenarios_dir: Path | None = None,
    profiles_dir: Path | None = None,
) -> list[dict[str, Any]]:
    packages = Path(package_root or _package_root())
    evidence_base = Path(evidence_root or _evidence_root())
    scenario_map = (
        _scenario_candidate_map(Path(scenarios_dir))
        if scenarios_dir is not None
        else _scenario_candidate_map_from_catalog()
    )
    profile_ids = _available_profile_ids(Path(profiles_dir or _profiles_dir()))
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in _source_files(packages):
        data, raw, diagnostics = _read_yaml_json(path)
        fallback_id = path.stem
        declared_id = data.get("id") if isinstance(data, dict) else None
        package_id = declared_id or fallback_id
        if not is_safe_asset_id(package_id):
            package_id = fallback_id if is_safe_asset_id(fallback_id) else f"invalid-{len(rows)}"
            diagnostics.append("id: unsafe or unavailable; source filename used for display")
        if package_id in seen_ids:
            diagnostics.append(f"id: duplicate package identifier {package_id!r}")
            package_id = fallback_id
            if package_id in seen_ids or not is_safe_asset_id(package_id):
                package_id = f"invalid-{len(rows)}"
        seen_ids.add(package_id)
        body = data if isinstance(data, dict) else {}
        candidate_ids = _string_list(body.get("candidate_ids"))
        scenario_ids = sorted({scenario for candidate in candidate_ids for scenario in scenario_map.get(candidate, [])})
        profile_id = body.get("runtime_profile", "") if isinstance(body.get("runtime_profile", ""), str) else ""
        evidence = [
            _evidence_record(evidence_base, package_id, index, value)
            for index, value in enumerate(_string_list(body.get("evidence_paths")))
        ]
        source_path = (
            f"dwarf/evidence-packages/{path.name}"
            if packages == PROJECT_ROOT / "dwarf" / "evidence-packages"
            else str(path)
        )
        relationships = [
            {
                "catalog": "scenarios",
                "id": scenario_id,
                "label": f"Scenario {scenario_id}",
                "url": f"/operate/scenarios/{scenario_id}",
                "relation": "exercised-by",
                "source_path": f"{source_path}#candidate_ids",
                "resolved": True,
            }
            for scenario_id in scenario_ids
        ]
        if profile_id:
            relationships.append(
                {
                    "catalog": "profiles",
                    "id": profile_id,
                    "label": f"Profile {profile_id}",
                    "url": f"/operate/profiles/{profile_id}",
                    "relation": "uses-profile",
                    "source_path": f"{source_path}#runtime_profile",
                    "resolved": profile_id in profile_ids,
                }
            )
        rows.append(
            {
                "id": package_id,
                "label": body.get("label") or path.stem,
                "runnable": body.get("runnable") is True,
                "run_state": "legacy read-only action" if body.get("runnable") is True else "status-only",
                "status_text": body.get("status") or "unavailable",
                "status": "diagnostic" if diagnostics else "ready",
                "candidate_ids": candidate_ids,
                "evidence": evidence,
                "available_evidence_count": sum(item["status"].startswith("available-") for item in evidence),
                "blockers": _string_list(body.get("blockers")),
                "read_only_actions": _string_list(body.get("read_only_actions")),
                "runtime_profile": profile_id,
                "runtime_root": body.get("runtime_root", "") if isinstance(body.get("runtime_root", ""), str) else "",
                "profile_id": profile_id or None,
                "profile_available": bool(profile_id and profile_id in profile_ids),
                "scenario_ids": scenario_ids,
                "relationships": relationships,
                "diagnostics": diagnostics,
                "raw": body,
                "declared_id": declared_id,
                "raw_text": raw,
                "source_file": str(path),
                "source_path": source_path,
                "export_path": f"dwarf/evidence-packages/{path.name}",
                "summary": f"{len(candidate_ids)} candidate risks · {len(evidence)} evidence references · {len(diagnostics)} diagnostics",
            }
        )
    return rows


def risk_package_detail(package_id: str, **kwargs) -> dict[str, Any] | None:
    if not is_safe_asset_id(package_id):
        return None
    return next((row for row in risk_package_catalog_rows(**kwargs) if row["id"] == package_id), None)


def deterministic_risk_package_archive(*, package_id: str | None = None, **kwargs) -> bytes:
    rows = risk_package_catalog_rows(**kwargs)
    if package_id is not None:
        rows = [row for row in rows if row["id"] == package_id]
    return deterministic_export_archive(
        "risk-packages",
        [
            ExportSource(
                object_id=row["id"],
                source_path=row["export_path"],
                export_path=row["export_path"],
                body=row["raw_text"].encode("utf-8"),
            )
            for row in rows
        ],
    )


def _attachment(filename: str) -> dict[str, str]:
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


def dispatch_risk_package_api_request(path: str, **kwargs):
    parts = urlsplit(path).path.strip("/").split("/")
    if parts == ["api", "risk-packages", "export"]:
        return 200, "application/gzip", deterministic_risk_package_archive(**kwargs), _attachment("dwarf-risk-work-packages.tar.gz")
    if len(parts) >= 3 and parts[:2] == ["api", "risk-packages"]:
        package_id = unquote(parts[2])
        if not is_safe_asset_id(package_id):
            return 400, "text/plain; charset=utf-8", b"invalid risk work package id\n"
        row = risk_package_detail(package_id, **kwargs)
        if row is None:
            return 404, "text/plain; charset=utf-8", b"not found\n"
        if len(parts) == 4 and parts[3] == "download":
            return 200, "application/yaml; charset=utf-8", Path(row["source_file"]).read_bytes(), _attachment(Path(row["source_file"]).name)
        if len(parts) == 4 and parts[3] == "export":
            return 200, "application/gzip", deterministic_risk_package_archive(package_id=package_id, **kwargs), _attachment(f"{package_id}.tar.gz")
        if len(parts) == 6 and parts[3] == "evidence" and _SAFE_INDEX.fullmatch(parts[4]) and parts[5] == "download":
            index = int(parts[4])
            if index >= len(row["evidence"]):
                return 404, "text/plain; charset=utf-8", b"not found\n"
            evidence = row["evidence"][index]
            if evidence["status"] != "available-file" or not evidence["resolved_path"]:
                return 404, "text/plain; charset=utf-8", b"not found\n"
            source = Path(evidence["resolved_path"])
            return 200, "application/octet-stream", source.read_bytes(), _attachment(source.name)
    return None


__all__ = [
    "deterministic_risk_package_archive",
    "dispatch_risk_package_api_request",
    "risk_package_catalog_rows",
    "risk_package_detail",
    "risk_work_package_schema",
]
