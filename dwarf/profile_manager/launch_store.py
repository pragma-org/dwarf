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

from profile_manager.config import storage_roots
from profile_manager.data.scenarios import _scenarios_dir


_LAUNCH_ID = re.compile(r"^launch-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class LaunchStoreError(ValueError):
    pass


def launch_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    configured = os.environ.get("ADA2_DWARF_LAUNCH_ROOT", "").strip()
    if configured:
        return Path(configured)
    return storage_roots().state / "launches"


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


def _copy_verified(
    source: str,
    destination: Path,
    *,
    expected_digest: str | None,
    executable: bool = False,
) -> str:
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise LaunchStoreError("prepared library target evidence is missing or unsafe")
    body = path.read_bytes()
    observed = _digest(body)
    if expected_digest is not None and observed != expected_digest:
        raise LaunchStoreError("prepared library target evidence digest changed")
    destination.write_bytes(body)
    destination.chmod(0o500 if executable else 0o400)
    return observed


def _freeze_prepared_library_targets(
    plan: dict[str, Any],
    prepared: list[dict[str, Any]],
    *,
    stage: Path,
    destination: Path,
) -> dict[str, Any]:
    """Copy verified build evidence into a controller-private launch snapshot."""

    requirements = (plan.get("scenario") or {}).get(
        "library_target_requirements"
    ) or []
    if not requirements:
        if prepared:
            raise LaunchStoreError("prepared library targets were not requested")
        return plan
    if not isinstance(prepared, list) or len(prepared) != len(requirements):
        raise LaunchStoreError("prepared library target count differs from the plan")

    derived = json.loads(json.dumps(plan))
    bindings = []
    retained = []
    frozen_by_key: dict[str, dict[str, Any]] = {}
    identity_fields = (
        "cache_key",
        "target_id",
        "implementation",
        "version",
        "source_repository",
        "source_revision",
        "platform",
        "recipe_digest",
        "builder_image",
        "builder_transform",
        "toolchain",
    )
    for requirement, result in zip(requirements, prepared, strict=True):
        if (
            not isinstance(requirement, dict)
            or not isinstance(result, dict)
            or result.get("location") != requirement.get("location")
            or any(result.get(field) != requirement.get(field) for field in identity_fields)
        ):
            raise LaunchStoreError("prepared library target identity differs from the plan")
        cache_key = str(requirement.get("cache_key") or "")
        key = cache_key.removeprefix("sha256:")
        if not _SHA256.fullmatch(cache_key):
            raise LaunchStoreError("prepared library target cache identity is invalid")
        frozen = frozen_by_key.get(cache_key)
        if frozen is None:
            root = stage / "library-targets" / key
            manifests = root / "manifests"
            manifests.mkdir(parents=True, mode=0o700)
            expected = {
                "executable": result.get("binary_sha256"),
                "build.log": result.get("build_log_sha256"),
                "upstream-Cargo.lock": result.get("upstream_lock_sha256"),
                "effective-Cargo.lock": result.get("effective_lock_sha256"),
            }
            sources = {
                "executable": result.get("binary"),
                "build.log": result.get("build_log"),
                "upstream-Cargo.lock": result.get("upstream_lock"),
                "effective-Cargo.lock": result.get("effective_lock"),
            }
            observed = {}
            for name, source in sources.items():
                digest = expected[name]
                if not isinstance(source, str) or not _SHA256.fullmatch(str(digest or "")):
                    raise LaunchStoreError("prepared library target evidence identity is invalid")
                observed[name] = _copy_verified(
                    source,
                    root / name,
                    expected_digest=digest,
                    executable=name == "executable",
                )
            receipt_source = result.get("receipt")
            receipt_expected = result.get("receipt_sha256")
            if (
                not isinstance(receipt_source, str)
                or not _SHA256.fullmatch(str(receipt_expected or ""))
            ):
                raise LaunchStoreError("prepared library target receipt is missing")
            receipt_digest = _copy_verified(
                receipt_source,
                root / "receipt.json",
                expected_digest=str(receipt_expected),
            )

            template_path = Path(str(requirement.get("template_manifest") or ""))
            if template_path.is_absolute() or ".." in template_path.parts:
                raise LaunchStoreError("library target manifest path is unsafe")
            template = Path(__file__).resolve().parents[2] / template_path
            if template.is_symlink() or not template.is_file():
                raise LaunchStoreError("library target manifest template is unavailable")
            recipe_inputs = {
                str(item.get("path") or ""): str(item.get("sha256") or "")
                for item in requirement.get("recipe_inputs") or []
                if isinstance(item, dict)
            }
            template_expected = recipe_inputs.get(template_path.as_posix())
            try:
                template_body = template.read_bytes()
                if (
                    not _SHA256.fullmatch(str(template_expected or ""))
                    or _digest(template_body) != template_expected
                ):
                    raise LaunchStoreError(
                        "library target manifest recipe identity changed"
                    )
                manifest = json.loads(template_body.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise LaunchStoreError("library target manifest template is invalid") from exc
            if manifest.get("id") != requirement.get("target_id"):
                raise LaunchStoreError("library target manifest identity differs")
            final_root = destination / "library-targets" / key
            manifest["binary"] = str((final_root / "executable").resolve())
            manifest["upstream_commit"] = requirement["source_revision"]
            manifest["dwarf_build_evidence"] = {
                "cache_key": cache_key,
                "version": requirement["version"],
                "source_revision": requirement["source_revision"],
                "source_tree": result.get("source_tree"),
                "recipe_digest": requirement["recipe_digest"],
                "builder_image": requirement["builder_image"],
                "builder_transform": requirement["builder_transform"],
                "selected_toolchain": result.get("selected_toolchain"),
                "rustc_version": result.get("rustc_version"),
                "cargo_version": result.get("cargo_version"),
                "build_command": result.get("build_command"),
                "binary_sha256": observed["executable"],
                "build_log_sha256": observed["build.log"],
                "upstream_lock_sha256": observed["upstream-Cargo.lock"],
                "effective_lock_sha256": observed["effective-Cargo.lock"],
                "receipt_sha256": receipt_digest,
            }
            manifest_path = manifests / f"{requirement['target_id']}.yaml"
            _write_private(manifest_path, _canonical_bytes(manifest))
            manifest_path.chmod(0o400)
            root.chmod(0o500)
            manifests.chmod(0o500)
            frozen = {
                "manifests_dir": str((final_root / "manifests").resolve()),
                "evidence": manifest["dwarf_build_evidence"],
            }
            frozen_by_key[cache_key] = frozen
        bindings.append(
            {
                "location": requirement["location"],
                "target_id": requirement["target_id"],
                "manifests_dir": frozen["manifests_dir"],
                "identity": frozen["evidence"],
            }
        )
        retained.append(
            {
                "location": requirement["location"],
                "target_id": requirement["target_id"],
                **frozen["evidence"],
            }
        )
    derived["scenario"]["library_target_bindings"] = bindings
    derived["scenario"]["prepared_library_targets"] = retained
    return derived


def materialized_scenario(plan: dict[str, Any]) -> bytes:
    scenario_id = str((plan.get("scenario") or {}).get("id") or "")
    source = _scenarios_dir() / f"{scenario_id}.yaml"
    if not source.is_file() or source.parent != _scenarios_dir():
        raise LaunchStoreError("scenario source is no longer in the active catalog")
    try:
        source_body = source.read_bytes()
        original = json.loads(source_body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LaunchStoreError("scenario source cannot be materialized") from exc
    if original.get("id") != scenario_id:
        raise LaunchStoreError("scenario source identifier changed")
    if (plan.get("scenario") or {}).get("digest") != _digest(source_body):
        raise LaunchStoreError("scenario source digest changed after plan resolution")

    materialized = json.loads(json.dumps(original))
    original_profile = original.get("profile")
    selected_profile = (plan.get("profile") or {}).get("id")
    if (
        isinstance(original_profile, str)
        and original_profile
        and isinstance(selected_profile, str)
        and selected_profile
        and selected_profile != original_profile
    ):
        for phase in ("setup", "load", "faults", "probes", "assertions", "teardown"):
            entries = materialized.get(phase)
            if not isinstance(entries, list):
                continue
            materialized[phase] = [
                {
                    **entry,
                    "profile_id": selected_profile,
                }
                if isinstance(entry, dict)
                and entry.get("profile_id") == original_profile
                else entry
                for entry in entries
            ]
    measurement_context = plan.get("measurements") or {}
    exact_measurements = {
        str(item.get("id")): item
        for item in (
            list(measurement_context.get("resolved") or [])
            + list(measurement_context.get("disabled") or [])
        )
        if isinstance(item, dict) and item.get("id")
    }
    materialized_measurements = []
    for requested in measurement_context.get("requested") or []:
        selected = exact_measurements.get(str(requested.get("id") or ""))
        if selected is None:
            continue
        materialized_measurements.append(
            {
                "id": selected["id"],
                "enabled": bool(selected.get("enabled")),
                "parameters": dict(selected.get("parameters") or {}),
                "threshold_gate": dict(
                    selected.get("threshold_gate")
                    or {"enabled": False, "thresholds": []}
                ),
            }
        )

    desired = {
        "target": dict(plan["scenario"]["target"]),
        "seed": plan["scenario"].get("seed"),
        "profile": (plan.get("profile") or {}).get("id"),
    }
    if measurement_context.get("profile") is not None:
        # Freeze the resolved tap set instead of allowing a mutable profile to
        # be resolved a second time by the canonical runner.
        desired["measurement_profile"] = "none"
        desired["measurements"] = materialized_measurements
    for field, selected in desired.items():
        if original.get(field) == selected:
            continue
        if selected is None:
            materialized.pop(field, None)
        else:
            materialized[field] = selected
    for binding in (plan.get("scenario") or {}).get("library_target_bindings") or []:
        location = binding.get("location")
        if not isinstance(location, list) or not isinstance(binding.get("manifests_dir"), str):
            raise LaunchStoreError("library target binding is invalid")
        entry = materialized
        try:
            for component in location:
                entry = entry[component]
        except (KeyError, IndexError, TypeError) as exc:
            raise LaunchStoreError("library target binding location changed") from exc
        if not isinstance(entry, dict) or entry.get("target_id") != binding.get("target_id"):
            raise LaunchStoreError("library target declaration changed")
        entry["manifests_dir"] = binding["manifests_dir"]
    for binding in (plan.get("scenario") or {}).get("target_bindings") or []:
        if binding.get("kind") != "resolved-plan":
            continue
        location = binding.get("location")
        declared = binding.get("declared")
        resolved = binding.get("resolved")
        if (
            not isinstance(location, list)
            or not isinstance(declared, dict)
            or not isinstance(resolved, dict)
        ):
            raise LaunchStoreError("resolved target binding is invalid")
        entry: Any = materialized
        try:
            for component in location:
                entry = entry[component]
        except (KeyError, IndexError, TypeError) as exc:
            raise LaunchStoreError("resolved target binding location changed") from exc
        if not isinstance(entry, dict) or entry.get("primitive") != "runtime_verify_exact_target":
            raise LaunchStoreError("resolved target binding no longer names its verifier")
        if entry.get("identity_binding") != "resolved-plan":
            raise LaunchStoreError("resolved target binding declaration changed")
        if any(entry.get(field) != value for field, value in declared.items()):
            raise LaunchStoreError("resolved target binding constraints changed")
        if any(resolved.get(field) != value for field, value in declared.items()):
            raise LaunchStoreError("resolved target binding violates its constraints")
        entry.update(resolved)
    for binding in (plan.get("scenario") or {}).get("substrate_bindings") or []:
        if binding.get("kind") != "resolved-plan":
            raise LaunchStoreError("resolved substrate binding kind is invalid")
        location = binding.get("location")
        declared = binding.get("declared")
        resolved = binding.get("resolved")
        if (
            not isinstance(location, list)
            or not isinstance(declared, dict)
            or not isinstance(resolved, dict)
        ):
            raise LaunchStoreError("resolved substrate binding is invalid")
        entry: Any = materialized
        try:
            for component in location:
                entry = entry[component]
        except (KeyError, IndexError, TypeError) as exc:
            raise LaunchStoreError("resolved substrate binding location changed") from exc
        if not isinstance(entry, dict) or any(
            entry.get(field) != value for field, value in declared.items()
        ):
            raise LaunchStoreError("resolved substrate binding declaration changed")
        registry_identity = {"version", "source_revision", "image", "image_digest"}
        local_identity = {
            "version",
            "source_revision",
            "image",
            "image_config_id",
            "executable_digest",
            "platform",
            "runtime_interface",
            "operator_registration_id",
            "controller_installation_id",
            "operator_overlay_revision",
        }
        required = frozenset(resolved)
        if (
            required not in {frozenset(registry_identity), frozenset(local_identity)}
            or any(not resolved[field] for field in required)
            or (
                required == local_identity
                and resolved["image"] != resolved["image_config_id"]
            )
        ):
            raise LaunchStoreError("resolved substrate binding identity is incomplete")
        entry.update(resolved)
    for binding in (plan.get("scenario") or {}).get(
        "required_conformance_adapters"
    ) or []:
        if binding.get("kind") != "resolved-registry":
            raise LaunchStoreError("conformance adapter binding kind is invalid")
        location = binding.get("location")
        declared = binding.get("declared")
        resolved = binding.get("resolved")
        if (
            not isinstance(location, list)
            or not isinstance(declared, dict)
            or not isinstance(resolved, dict)
        ):
            raise LaunchStoreError("conformance adapter binding is not launch-ready")
        entry = materialized
        try:
            for component in location:
                entry = entry[component]
        except (KeyError, IndexError, TypeError) as exc:
            raise LaunchStoreError("conformance adapter binding location changed") from exc
        adapter_family = str(binding.get("adapter_family") or "plutus")
        if adapter_family == "plutus":
            valid_declaration = (
                isinstance(entry, dict)
                and location[-1] == declared.get("implementation")
                and all(
                    entry.get(field) == value
                    for field, value in declared.items()
                    if field != "implementation"
                )
            )
        elif adapter_family == "cbor":
            valid_declaration = (
                isinstance(entry, dict)
                and entry.get("primitive") == "runtime_version_pinned_cbor_conformance"
                and all(entry.get(field) == value for field, value in declared.items())
            )
        else:
            valid_declaration = False
        if not valid_declaration:
            raise LaunchStoreError("conformance adapter declaration changed")
        entry["expected_provenance"] = resolved
    if materialized == original:
        return source_body
    return _canonical_bytes(materialized)


def expected_launch_artifacts(plan: dict[str, Any]) -> dict[str, bytes]:
    """Materialize the exact controller-verifiable launch input set."""

    artifacts = {"scenario.yaml": materialized_scenario(plan)}
    profile = plan.get("profile")
    if profile:
        artifacts["profile.json"] = _canonical_bytes(profile["snapshot"])
    return artifacts


def _store_launch(
    plan: dict[str, Any],
    *,
    launch_id: str,
    base: Path,
    preflight: dict[str, Any] | None = None,
    prepared_library_targets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise LaunchStoreError("resolved run plan is invalid")
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    destination = base / launch_id
    if destination.exists():
        raise LaunchStoreError("launch snapshot already exists")
    stage = base / f".{launch_id}.tmp-{secrets.token_hex(4)}"
    stage.mkdir(mode=0o700)
    try:
        stored_source = plan
        if prepared_library_targets is not None:
            stored_source = _freeze_prepared_library_targets(
                plan,
                prepared_library_targets,
                stage=stage,
                destination=destination,
            )
        expected_artifacts = expected_launch_artifacts(stored_source)
        scenario_body = expected_artifacts["scenario.yaml"]
        _write_private(stage / "scenario.yaml", scenario_body)

        profile_body = expected_artifacts.get("profile.json")
        if profile_body is not None:
            _write_private(stage / "profile.json", profile_body)

        stored_plan = json.loads(json.dumps(stored_source))
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
        if preflight is not None:
            if preflight.get("state") not in {"ready", "blocked"}:
                raise LaunchStoreError("launch preflight result is invalid")
            _write_private(stage / "preflight.json", _canonical_bytes(preflight))
        os.replace(stage, destination)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"launch_id": launch_id, "path": destination, "plan": stored_plan}


def materialize_launch_snapshot(
    plan: dict[str, Any],
    *,
    launch_id: str,
    root: str | Path,
    preflight: dict[str, Any] | None = None,
    prepared_library_targets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Regenerate trusted launch inputs below a controller-private root."""

    return _store_launch(
        plan,
        launch_id=validate_launch_id(launch_id),
        base=launch_root(root),
        preflight=preflight,
        prepared_library_targets=prepared_library_targets,
    )


def create_launch(
    plan: dict[str, Any], *, root: str | Path | None = None
) -> dict[str, Any]:
    """Freeze a resolved plan and inputs below an unpredictable strict ID."""
    base = launch_root(root)
    for _ in range(8):
        launch_id = f"launch-{secrets.token_hex(12)}"
        destination = base / launch_id
        if not destination.exists():
            break
    else:
        raise LaunchStoreError("could not allocate a unique launch identifier")

    return _store_launch(plan, launch_id=launch_id, base=base)


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


def measurement_resolution_from_environment(
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Load the immutable launch measurement resolution, when launched normally."""
    environment = os.environ if env is None else env
    launch_id = str(environment.get("ADA2_DWARF_LAUNCH_ID") or "").strip()
    if not launch_id:
        return None
    root = str(environment.get("ADA2_DWARF_LAUNCH_ROOT") or "").strip() or None
    launch = load_launch(launch_id, root=root)
    measurements = launch["plan"].get("measurements")
    if not isinstance(measurements, dict):
        raise LaunchStoreError("launch measurement resolution is missing")
    return json.loads(json.dumps(measurements))


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
    prepared = (launch["plan"].get("scenario") or {}).get(
        "prepared_library_targets"
    ) or []
    retained_keys = set()
    for item in prepared:
        cache_key = str(item.get("cache_key") or "")
        key = cache_key.removeprefix("sha256:")
        target_id = str(item.get("target_id") or "")
        if (
            not _SHA256.fullmatch(cache_key)
            or not target_id
            or cache_key in retained_keys
        ):
            if cache_key in retained_keys:
                continue
            raise LaunchStoreError("retained library target identity is invalid")
        retained_keys.add(cache_key)
        source_root = launch["directory"] / "library-targets" / key
        source_manifest = source_root / "manifests" / f"{target_id}.yaml"
        expected_files = {
            "executable": item.get("binary_sha256"),
            "build.log": item.get("build_log_sha256"),
            "upstream-Cargo.lock": item.get("upstream_lock_sha256"),
            "effective-Cargo.lock": item.get("effective_lock_sha256"),
            "receipt.json": item.get("receipt_sha256"),
        }
        if (
            source_root.is_symlink()
            or not source_root.is_dir()
            or source_manifest.is_symlink()
            or not source_manifest.is_file()
            or any(not _SHA256.fullmatch(str(value or "")) for value in expected_files.values())
        ):
            raise LaunchStoreError("retained library target evidence is invalid")
        try:
            manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LaunchStoreError("retained library target manifest is invalid") from exc
        expected_binary = source_root / "executable"
        if (
            manifest.get("id") != target_id
            or manifest.get("upstream_commit") != item.get("source_revision")
            or Path(str(manifest.get("binary") or "")) != expected_binary.resolve()
            or manifest.get("dwarf_build_evidence") != {
                key: value
                for key, value in item.items()
                if key not in {"location", "target_id"}
            }
        ):
            raise LaunchStoreError("retained library target manifest identity differs")
        target_root = destination / "library-targets" / key
        target_manifests = target_root / "manifests"
        target_manifests.mkdir(parents=True, mode=0o700)
        for name, expected_digest in expected_files.items():
            _copy_verified(
                str(source_root / name),
                target_root / name,
                expected_digest=str(expected_digest),
                executable=name == "executable",
            )
        _copy_verified(
            str(source_manifest),
            target_manifests / source_manifest.name,
            expected_digest=None,
        )
        target_manifests.chmod(0o500)
        target_root.chmod(0o500)
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
