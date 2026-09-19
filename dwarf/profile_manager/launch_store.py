"""Atomic, identifier-only launch snapshots for the restricted host shim."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from profile_manager.data.scenarios import _scenarios_dir


_LAUNCH_ID = re.compile(r"^launch-[0-9a-f]{24}$")


class LaunchStoreError(ValueError):
    pass


def launch_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    configured = os.environ.get("ADA2_DWARF_LAUNCH_ROOT", "").strip()
    if configured:
        return Path(configured)
    state = Path(os.environ.get("ADA2_DWARF_STATE_DIR") or "/var/dwarf/state")
    return state / "launches"


def validate_launch_id(launch_id: str) -> str:
    if not isinstance(launch_id, str) or not _LAUNCH_ID.fullmatch(launch_id):
        raise LaunchStoreError("invalid launch identifier")
    return launch_id


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _write_private(path: Path, body: bytes) -> None:
    path.write_bytes(body)
    path.chmod(0o600)


def _materialized_scenario(plan: dict[str, Any]) -> bytes:
    scenario_id = str((plan.get("scenario") or {}).get("id") or "")
    source = _scenarios_dir() / f"{scenario_id}.yaml"
    if not source.is_file() or source.parent != _scenarios_dir():
        raise LaunchStoreError("scenario source is no longer in the active catalog")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaunchStoreError("scenario source cannot be materialized") from exc
    if value.get("id") != scenario_id:
        raise LaunchStoreError("scenario source identifier changed")
    value["target"] = dict(plan["scenario"]["target"])
    profile = plan.get("profile")
    if profile:
        value["profile"] = profile["id"]
    measurement_profile = ((plan.get("measurements") or {}).get("profile") or {}).get("id")
    if measurement_profile:
        value["measurement_profile"] = measurement_profile
    return _canonical_bytes(value)


def create_launch(
    plan: dict[str, Any], *, root: str | Path | None = None
) -> dict[str, Any]:
    """Freeze a resolved plan and inputs below an unpredictable strict ID."""
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise LaunchStoreError("resolved run plan is invalid")
    base = launch_root(root)
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    for _ in range(8):
        launch_id = f"launch-{secrets.token_hex(12)}"
        destination = base / launch_id
        if not destination.exists():
            break
    else:
        raise LaunchStoreError("could not allocate a unique launch identifier")

    stage = base / f".{launch_id}.tmp-{secrets.token_hex(4)}"
    stage.mkdir(mode=0o700)
    try:
        scenario_body = _materialized_scenario(plan)
        _write_private(stage / "scenario.yaml", scenario_body)

        profile_body = None
        profile = plan.get("profile")
        if profile:
            profile_body = _canonical_bytes(profile["snapshot"])
            _write_private(stage / "profile.json", profile_body)

        stored_plan = json.loads(json.dumps(plan))
        stored_plan.update(
            {
                "launch_id": launch_id,
                "created_at": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "artifacts": {
                    "scenario.yaml": _digest(scenario_body),
                    **(
                        {"profile.json": _digest(profile_body)}
                        if profile_body is not None
                        else {}
                    ),
                },
            }
        )
        _write_private(stage / "plan.json", _canonical_bytes(stored_plan))
        os.replace(stage, destination)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"launch_id": launch_id, "path": destination, "plan": stored_plan}


def load_launch(
    launch_id: str, *, root: str | Path | None = None
) -> dict[str, Any]:
    launch_id = validate_launch_id(launch_id)
    base = launch_root(root).resolve()
    directory = base / launch_id
    if directory.is_symlink() or not directory.is_dir():
        raise LaunchStoreError("launch does not exist")
    if directory.resolve().parent != base:
        raise LaunchStoreError("launch escaped its configured root")

    plan_path = directory / "plan.json"
    scenario_path = directory / "scenario.yaml"
    profile_path = directory / "profile.json"
    for path in (plan_path, scenario_path):
        if path.is_symlink() or not path.is_file():
            raise LaunchStoreError(f"launch input is missing: {path.name}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaunchStoreError("launch plan is invalid") from exc
    if plan.get("launch_id") != launch_id:
        raise LaunchStoreError("launch plan identifier does not match its directory")
    artifacts = plan.get("artifacts") or {}
    for name, path in (("scenario.yaml", scenario_path), ("profile.json", profile_path)):
        expected = artifacts.get(name)
        if expected is None:
            if name == "profile.json" and not path.exists():
                continue
            raise LaunchStoreError(f"launch plan does not declare {name}")
        if path.is_symlink() or not path.is_file():
            raise LaunchStoreError(f"launch input is missing: {name}")
        if _digest(path.read_bytes()) != expected:
            raise LaunchStoreError(f"launch input digest mismatch: {name}")
    return {
        "launch_id": launch_id,
        "directory": directory,
        "plan": plan,
        "plan_path": plan_path,
        "scenario_path": scenario_path,
        "profile_path": profile_path if profile_path.is_file() else None,
    }


def retain_launch_inputs(
    launch_id: str,
    *,
    run_dir: str | Path,
    root: str | Path | None = None,
) -> Path:
    launch = load_launch(launch_id, root=root)
    destination = Path(run_dir) / "launch"
    destination.mkdir(mode=0o700, exist_ok=False)
    for source in (
        launch["plan_path"],
        launch["scenario_path"],
        launch["profile_path"],
    ):
        if source is None:
            continue
        target = destination / source.name
        shutil.copyfile(source, target)
        target.chmod(0o600)
    return destination


def retain_launch_inputs_from_environment(run_dir: str | Path) -> Path | None:
    launch_id = os.environ.get("ADA2_DWARF_LAUNCH_ID", "").strip()
    if not launch_id:
        return None
    root = os.environ.get("ADA2_DWARF_LAUNCH_ROOT", "").strip() or None
    return retain_launch_inputs(launch_id, run_dir=run_dir, root=root)
