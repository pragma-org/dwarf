"""Atomic, identifier-only launch snapshots for the restricted host shim."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
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
    preflight_path = launch["directory"] / "preflight.json"
    for source in (
        launch["plan_path"],
        launch["scenario_path"],
        launch["profile_path"],
        preflight_path if preflight_path.is_file() else None,
    ):
        if source is None:
            continue
        target = destination / source.name
        shutil.copyfile(source, target)
        target.chmod(0o600)
    return destination


def record_launch_preflight(
    launch_id: str,
    result: dict[str, Any],
    *,
    root: str | Path | None = None,
) -> Path:
    if not isinstance(result, dict) or result.get("state") not in {"ready", "blocked"}:
        raise LaunchStoreError("launch preflight result is invalid")
    launch = load_launch(launch_id, root=root)
    path = launch["directory"] / "preflight.json"
    temporary = launch["directory"] / ".preflight.json.tmp"
    _write_private(temporary, _canonical_bytes(result))
    os.replace(temporary, path)
    return path


def _default_process_checker(node: dict[str, Any]) -> bool:
    container = str(node.get("container_name") or "").strip()
    if container:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"
    pid_file = str(node.get("pid_file") or "").strip()
    if pid_file:
        try:
            pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            return False
        return True
    return False


def check_launch_readiness(
    launch_id: str,
    *,
    root: str | Path | None = None,
    process_checker=None,
) -> dict[str, Any]:
    """Check a profile-bound launch against current host runtime evidence."""
    launch = load_launch(launch_id, root=root)
    plan = launch["plan"]
    profile = plan.get("profile")
    checks: list[dict[str, Any]] = []
    if not profile:
        return {
            "state": "ready",
            "launch_id": launch_id,
            "checks": [{"id": "profile-not-required", "passed": True}],
        }

    runtime_root = Path(str((profile.get("snapshot") or {}).get("remote_runtime_root") or ""))
    runtime_path = runtime_root / "runtime.json"
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        metadata_ok = isinstance(runtime, dict)
    except (OSError, json.JSONDecodeError):
        runtime = {}
        metadata_ok = False
    checks.append(
        {
            "id": "runtime-metadata",
            "passed": metadata_ok,
            "detail": str(runtime_path),
        }
    )
    profile_ok = metadata_ok and runtime.get("profile_id") == profile.get("id")
    checks.append(
        {
            "id": "profile-identity",
            "passed": profile_ok,
            "expected": profile.get("id"),
            "observed": runtime.get("profile_id"),
        }
    )

    observed_nodes = ((runtime.get("version_provenance") or {}).get("nodes") or [])
    identity_failures = []
    for implementation, expected in ((plan.get("versions") or {}).get("resolved") or {}).items():
        matches = [
            node
            for node in observed_nodes
            if node.get("implementation") == implementation and not node.get("supporting")
        ]
        if not matches:
            identity_failures.append(f"{implementation}: no proven runtime node")
            continue
        for node in matches:
            observed = {
                "version": node.get("resolved_version") or node.get("requested_version"),
                "source_revision": node.get("source_revision"),
                "image_digest": node.get("image_digest"),
            }
            for key in ("version", "source_revision", "image_digest"):
                if expected.get(key) and observed.get(key) != expected.get(key):
                    identity_failures.append(
                        f"{node.get('id') or implementation}: {key} is {observed.get(key)!r}, expected {expected.get(key)!r}"
                    )
            if node.get("identity_status") not in {
                "running-version-verified",
                "running-image-verified",
            }:
                identity_failures.append(
                    f"{node.get('id') or implementation}: runtime identity is not verified"
                )
    checks.append(
        {
            "id": "exact-version-identity",
            "passed": not identity_failures and metadata_ok,
            "detail": identity_failures,
        }
    )

    checker = process_checker or _default_process_checker
    target_implementations = set(((plan.get("versions") or {}).get("resolved") or {}))
    runtime_nodes = [
        node
        for node in runtime.get("nodes") or []
        if node.get("impl") in target_implementations and not node.get("supporting")
    ]
    dead = [str(node.get("id") or "unknown") for node in runtime_nodes if not checker(node)]
    checks.append(
        {
            "id": "target-process-liveness",
            "passed": bool(runtime_nodes) and not dead,
            "detail": dead,
        }
    )
    return {
        "state": "ready" if all(check["passed"] for check in checks) else "blocked",
        "launch_id": launch_id,
        "profile_id": profile.get("id"),
        "checks": checks,
    }


def retain_launch_inputs_from_environment(run_dir: str | Path) -> Path | None:
    launch_id = os.environ.get("ADA2_DWARF_LAUNCH_ID", "").strip()
    if not launch_id:
        return None
    root = os.environ.get("ADA2_DWARF_LAUNCH_ROOT", "").strip() or None
    return retain_launch_inputs(launch_id, run_dir=run_dir, root=root)


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight",))
    parser.add_argument("launch_id")
    args = parser.parse_args(argv)
    result = check_launch_readiness(args.launch_id)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["state"] == "ready" else 78


if __name__ == "__main__":
    raise SystemExit(_main())
