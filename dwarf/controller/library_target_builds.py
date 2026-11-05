"""Controller-owned preparation and validation of exact library targets."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import time
import uuid
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_TARGET = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
_EXECUTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLATFORM = re.compile(r"^linux/(?:amd64|arm64)$")
_MAX_RECEIPT = 1024 * 1024
_MAX_LOG = 8 * 1024 * 1024
_MAX_BINARY = 256 * 1024 * 1024


class LibraryTargetBuildError(RuntimeError):
    """An exact library target could not be built or safely reused."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _artifact_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    """Remove only scenario placement from the reusable artifact identity."""

    return {key: value for key, value in requirement.items() if key != "location"}


def _expected_build_command(toolchain: str) -> list[str]:
    return [
        "cargo",
        f"+{toolchain}",
        "build",
        "--locked",
        "--release",
        "-p",
        "dwarf-amaru-library-target",
    ]


def _atomic_json(path: Path, value: object, *, mode: int = 0o400) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_root(runtime) -> Path:
    root = Path(runtime.state_dir) / "library-targets"
    if root.is_symlink():
        raise LibraryTargetBuildError("library target cache root cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _validate_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(requirement, dict):
        raise LibraryTargetBuildError("library target requirement must be an object")
    required = {
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
        "cache_key",
        "location",
        "binary_name",
        "source_path",
        "template_manifest",
        "template_upstream_commit",
        "recipe_inputs",
    }
    if set(requirement) != required:
        raise LibraryTargetBuildError("library target requirement fields are invalid")
    if (
        not _TARGET.fullmatch(str(requirement["target_id"]))
        or requirement["binary_name"] != requirement["target_id"]
        or requirement["implementation"] != "amaru"
        or requirement["source_repository"]
        != "https://github.com/pragma-org/amaru.git"
        or not _REVISION.fullmatch(str(requirement["source_revision"]))
        or not _PLATFORM.fullmatch(str(requirement["platform"]))
        or not _DIGEST.fullmatch(str(requirement["recipe_digest"]))
        or not _DIGEST.fullmatch(str(requirement["cache_key"]))
        or not isinstance(requirement["version"], str)
        or not requirement["version"]
        or not isinstance(requirement["toolchain"], str)
        or not requirement["toolchain"]
        or not isinstance(requirement["builder_image"], str)
        or "@sha256:" not in requirement["builder_image"]
        or requirement["builder_transform"] != "amaru-library-target-v2"
        or requirement["template_upstream_commit"] != "local"
        or not isinstance(requirement["location"], list)
        or not isinstance(requirement["recipe_inputs"], list)
        or not requirement["recipe_inputs"]
    ):
        raise LibraryTargetBuildError("library target requirement identity is invalid")
    for value in (requirement["source_path"], requirement["template_manifest"]):
        path = Path(str(value))
        if path.is_absolute() or ".." in path.parts:
            raise LibraryTargetBuildError("library target recipe path is unsafe")
    for item in requirement["recipe_inputs"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or Path(str(item["path"])).is_absolute()
            or ".." in Path(str(item["path"])).parts
            or not _DIGEST.fullmatch(str(item["sha256"]))
        ):
            raise LibraryTargetBuildError("library target recipe input is invalid")
    identity = {
        key: requirement[key]
        for key in (
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
    }
    if _digest_bytes(_canonical(identity)) != requirement["cache_key"]:
        raise LibraryTargetBuildError("library target cache key differs from its identity")
    return json.loads(json.dumps(requirement))


def _regular(path: Path, *, maximum: int, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise LibraryTargetBuildError(f"cached {label} is missing or unsafe")
    size = path.stat().st_size
    if size <= 0 or size > maximum:
        raise LibraryTargetBuildError(f"cached {label} size is invalid")


def _validate_trusted_recipe_inputs(requirement: dict[str, Any]) -> None:
    for item in requirement["recipe_inputs"]:
        path = ROOT / item["path"]
        try:
            path.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise LibraryTargetBuildError(
                "trusted library target recipe is unavailable"
            ) from exc
        if (
            path.is_symlink()
            or not path.is_file()
            or _digest_file(path) != item["sha256"]
        ):
            raise LibraryTargetBuildError(
                "trusted library target recipe identity changed"
            )


def _read_receipt(path: Path) -> dict[str, Any]:
    _regular(path, maximum=_MAX_RECEIPT, label="receipt")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryTargetBuildError("cached receipt is invalid") from exc
    if not isinstance(value, dict):
        raise LibraryTargetBuildError("cached receipt is invalid")
    return value


def _validated_cache(
    directory: Path, requirement: dict[str, Any], installation_id: str
) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise LibraryTargetBuildError("library target cache entry is unsafe")
    binary = directory / "executable"
    log = directory / "build.log"
    upstream_lock = directory / "upstream-Cargo.lock"
    effective_lock = directory / "effective-Cargo.lock"
    receipt_path = directory / "receipt.json"
    receipt = _read_receipt(receipt_path)
    expected = {
        "schema_version": "dwarf.library-target-build.v1",
        "cache_key": requirement["cache_key"],
        "target_id": requirement["target_id"],
        "implementation": requirement["implementation"],
        "version": requirement["version"],
        "source_repository": requirement["source_repository"],
        "source_revision": requirement["source_revision"],
        "platform": requirement["platform"],
        "recipe_digest": requirement["recipe_digest"],
        "builder_image": requirement["builder_image"],
        "toolchain": requirement["toolchain"],
        "controller_installation_id": installation_id,
        "requirement_digest": _digest_bytes(
            _canonical(_artifact_requirement(requirement))
        ),
        "builder_transform": requirement["builder_transform"],
    }
    if receipt.get("source_revision") != requirement["source_revision"]:
        raise LibraryTargetBuildError("cached library target source identity differs")
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise LibraryTargetBuildError("cached library target identity differs")
    if not _REVISION.fullmatch(str(receipt.get("source_commit") or "")):
        raise LibraryTargetBuildError("cached library target source commit is invalid")
    if receipt["source_commit"] != requirement["source_revision"]:
        raise LibraryTargetBuildError("cached library target source identity differs")
    if not _REVISION.fullmatch(str(receipt.get("source_tree") or "")):
        raise LibraryTargetBuildError("cached library target source tree is invalid")
    if not _DIGEST.fullmatch(str(receipt.get("binary_sha256") or "")):
        raise LibraryTargetBuildError("cached library target binary identity is invalid")
    if not _DIGEST.fullmatch(str(receipt.get("build_log_sha256") or "")):
        raise LibraryTargetBuildError("cached library target build log identity is invalid")
    if (
        not _DIGEST.fullmatch(str(receipt.get("upstream_lock_sha256") or ""))
        or not _DIGEST.fullmatch(str(receipt.get("effective_lock_sha256") or ""))
        or not isinstance(receipt.get("selected_toolchain"), str)
        or not receipt["selected_toolchain"]
        or not isinstance(receipt.get("rustc_version"), str)
        or not receipt["rustc_version"]
        or not isinstance(receipt.get("cargo_version"), str)
        or not receipt["cargo_version"]
        or receipt.get("build_command")
        != _expected_build_command(str(receipt.get("selected_toolchain") or ""))
    ):
        raise LibraryTargetBuildError("cached library target toolchain identity is invalid")
    _regular(binary, maximum=_MAX_BINARY, label="binary")
    _regular(log, maximum=_MAX_LOG, label="build log")
    _regular(upstream_lock, maximum=_MAX_LOG, label="upstream Cargo lock")
    _regular(effective_lock, maximum=_MAX_LOG, label="effective Cargo lock")
    if _digest_file(binary) != receipt["binary_sha256"]:
        raise LibraryTargetBuildError("cached library target binary digest differs")
    if _digest_file(log) != receipt["build_log_sha256"]:
        raise LibraryTargetBuildError("cached library target build log digest differs")
    if _digest_file(upstream_lock) != receipt["upstream_lock_sha256"]:
        raise LibraryTargetBuildError("cached library target upstream Cargo lock digest differs")
    if _digest_file(effective_lock) != receipt["effective_lock_sha256"]:
        raise LibraryTargetBuildError("cached library target Cargo lock digest differs")
    if not os.access(binary, os.X_OK):
        raise LibraryTargetBuildError("cached library target binary is not executable")
    return {
        **receipt,
        "binary": str(binary.resolve()),
        "receipt": str(receipt_path.resolve()),
        "receipt_sha256": _digest_file(receipt_path),
        "build_log": str(log.resolve()),
        "upstream_lock": str(upstream_lock.resolve()),
        "effective_lock": str(effective_lock.resolve()),
    }


def _acquire_lock(
    path: Path, *, deadline: float, cancel_event: threading.Event
):
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise LibraryTargetBuildError("library target lock file is unsafe") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise LibraryTargetBuildError("library target lock file is unsafe")
    while True:
        if cancel_event.is_set():
            os.close(descriptor)
            raise LibraryTargetBuildError("library target preparation was cancelled")
        if time.monotonic() >= deadline:
            os.close(descriptor)
            raise LibraryTargetBuildError("library target cache lock timed out")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            time.sleep(0.05)


def _bounded_result(path: Path) -> dict[str, Any]:
    _regular(path, maximum=_MAX_RECEIPT, label="builder result")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryTargetBuildError("library target builder result is invalid") from exc
    if not isinstance(value, dict):
        raise LibraryTargetBuildError("library target builder result is invalid")
    return value


def _container_name(runtime, requirement: dict[str, Any]) -> str:
    identity = f"{runtime.installation_id}:{requirement['cache_key']}"
    return "dwarf-library-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _builder_labels(runtime, requirement: dict[str, Any]) -> dict[str, str]:
    return {
        "io.dwarf.installation": str(runtime.installation_id),
        "io.dwarf.library-target": requirement["cache_key"],
    }


def _cleanup_builder_container(runtime, requirement: dict[str, Any], name: str) -> None:
    inspected = runtime._run(("container", "inspect", name), timeout=20, output_limit=_MAX_RECEIPT)
    if inspected.returncode:
        return
    try:
        rows = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise LibraryTargetBuildError("library target builder ownership is invalid") from exc
    if not isinstance(rows, list) or len(rows) != 1:
        raise LibraryTargetBuildError("library target builder ownership is ambiguous")
    labels = (rows[0].get("Config") or {}).get("Labels") or {}
    if any(labels.get(key) != value for key, value in _builder_labels(runtime, requirement).items()):
        raise LibraryTargetBuildError("library target builder ownership differs")
    removed = runtime._run(("rm", "-f", "-v", name), timeout=30, output_limit=_MAX_RECEIPT)
    if removed.returncode:
        raise LibraryTargetBuildError("library target builder cleanup failed")


def _restore_builder_workspace_ownership(
    runtime,
    requirement: dict[str, Any],
    host_workspace: Path,
) -> None:
    name = _container_name(runtime, requirement) + "-ownership"
    arguments = [
        "run",
        "--rm",
        "--name",
        name,
        "--platform",
        requirement["platform"],
        "--network",
        "none",
        "--cpus",
        "0.25",
        "--memory",
        "128m",
        "--pids-limit",
        "32",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "CHOWN",
        "--cap-add",
        "DAC_OVERRIDE",
    ]
    for key, value in _builder_labels(runtime, requirement).items():
        arguments += ["--label", f"{key}={value}"]
    arguments += [
        "--mount",
        f"type=bind,src={host_workspace},dst=/work",
        "--entrypoint",
        "chown",
        requirement["builder_image"],
        "-R",
        "--no-dereference",
        f"{os.getuid()}:{os.getgid()}",
        "/work",
    ]
    try:
        restored = runtime._run(
            tuple(arguments), timeout=120, output_limit=_MAX_RECEIPT
        )
    finally:
        _cleanup_builder_container(runtime, requirement, name)
    if restored.returncode:
        raise LibraryTargetBuildError(
            "library target workspace ownership cleanup failed"
        )


def _default_builder(
    requirement: dict[str, Any],
    workspace: Path,
    log_path: Path,
    *,
    runtime,
    execution_id: str,
    deadline: float,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    """Run the trusted build transform in a bounded controller-host container."""

    if cancel_event.is_set():
        raise LibraryTargetBuildError("library target preparation was cancelled")
    _validate_trusted_recipe_inputs(requirement)
    input_paths = {item["path"]: item["sha256"] for item in requirement["recipe_inputs"]}
    builder_relative = "dwarf/scripts/amaru_library_target_builder.py"
    source_relative = requirement["source_path"]
    if builder_relative not in input_paths or source_relative not in input_paths:
        raise LibraryTargetBuildError("trusted library target builder inputs are incomplete")
    shutil.copyfile(ROOT / builder_relative, workspace / "builder.py")
    shutil.copyfile(ROOT / source_relative, workspace / "target.rs")
    request = {
        "schema_version": "dwarf.library-target-builder-request.v1",
        "transform_version": requirement["builder_transform"],
        "source_repository": requirement["source_repository"],
        "source_revision": requirement["source_revision"],
        "target_id": requirement["target_id"],
        "target_source_sha256": input_paths[source_relative],
    }
    _atomic_json(workspace / "request.json", request, mode=0o600)
    try:
        relative_workspace = workspace.resolve().relative_to(Path(runtime.state_dir).resolve())
    except ValueError as exc:
        raise LibraryTargetBuildError("library target workspace is outside controller state") from exc
    host_workspace = Path(runtime.host_state_dir) / relative_workspace
    name = _container_name(runtime, requirement)
    from controller.build_resources import build_resources, docker_limit_arguments
    arguments = [
        "run",
        "--name",
        name,
        "--platform",
        requirement["platform"],
        *docker_limit_arguments(build_resources()),
        "--security-opt",
        "no-new-privileges",
        "--log-opt",
        "max-size=8m",
        "--log-opt",
        "max-file=1",
    ]
    for key, value in _builder_labels(runtime, requirement).items():
        arguments += ["--label", f"{key}={value}"]
    arguments += [
        "--mount",
        f"type=bind,src={host_workspace},dst=/work",
        requirement["builder_image"],
        "python",
        "/work/builder.py",
    ]
    remaining = max(1, int(deadline - time.monotonic()))
    result = None
    workspace.chmod(0o755)
    try:
        result = runtime._run(
            tuple(arguments),
            timeout=remaining,
            execution_id=execution_id,
            output_limit=_MAX_LOG,
            output_path=log_path,
        )
        if not log_path.exists():
            output = (result.stdout + result.stderr).encode("utf-8", errors="replace")
            log_path.write_bytes(output[-_MAX_LOG:])
        if cancel_event.is_set():
            raise LibraryTargetBuildError("library target preparation was cancelled")
        if result.returncode:
            raise LibraryTargetBuildError(
                "selected Amaru source is incompatible with the packaged library target; "
                "see the retained build log"
            )
    finally:
        try:
            _cleanup_builder_container(runtime, requirement, name)
        finally:
            try:
                _restore_builder_workspace_ownership(
                    runtime, requirement, host_workspace
                )
            finally:
                workspace.chmod(0o700)
    built = workspace / "executable"
    result_record = _bounded_result(workspace / "result.json")
    upstream_lock = workspace / "upstream-Cargo.lock"
    effective_lock = workspace / "effective-Cargo.lock"
    _regular(built, maximum=_MAX_BINARY, label="built binary")
    _regular(upstream_lock, maximum=_MAX_LOG, label="upstream Cargo lock")
    _regular(effective_lock, maximum=_MAX_LOG, label="effective Cargo lock")
    expected_result = {
        "schema_version": "dwarf.library-target-builder-result.v1",
        "transform_version": requirement["builder_transform"],
        "source_commit": requirement["source_revision"],
        "target_id": requirement["target_id"],
    }
    if any(result_record.get(key) != value for key, value in expected_result.items()):
        raise LibraryTargetBuildError("library target builder result identity differs")
    if result_record.get("upstream_lock_sha256") != _digest_file(upstream_lock):
        raise LibraryTargetBuildError(
            "library target builder upstream Cargo lock identity differs"
        )
    if result_record.get("effective_lock_sha256") != _digest_file(effective_lock):
        raise LibraryTargetBuildError(
            "library target builder effective Cargo lock identity differs"
        )
    if (
        not _REVISION.fullmatch(str(result_record.get("source_tree") or ""))
        or result_record.get("binary_sha256") != _digest_file(built)
        or not isinstance(result_record.get("toolchain"), str)
        or not result_record["toolchain"]
        or not isinstance(result_record.get("rustc_version"), str)
        or not result_record["rustc_version"]
        or not isinstance(result_record.get("cargo_version"), str)
        or not result_record["cargo_version"]
        or not isinstance(result_record.get("build_command"), list)
    ):
        raise LibraryTargetBuildError("library target builder output identity is invalid")
    if result_record["build_command"] != _expected_build_command(
        result_record["toolchain"]
    ):
        raise LibraryTargetBuildError("library target builder build command is invalid")
    return {
        "source_commit": result_record["source_commit"],
        "source_tree": result_record["source_tree"],
        "selected_toolchain": result_record["toolchain"],
        "rustc_version": result_record["rustc_version"],
        "cargo_version": result_record["cargo_version"],
        "upstream_lock_sha256": result_record["upstream_lock_sha256"],
        "effective_lock_sha256": result_record["effective_lock_sha256"],
        "build_command": result_record["build_command"],
    }


def _retain_failure(
    root: Path,
    execution_id: str,
    requirement: dict[str, Any],
    log_path: Path,
    error: BaseException,
) -> None:
    failure = root / "failures" / execution_id / requirement["cache_key"].removeprefix(
        "sha256:"
    )
    failure.mkdir(parents=True, exist_ok=False, mode=0o700)
    retained_log = failure / "build.log"
    if log_path.is_file() and not log_path.is_symlink():
        with log_path.open("rb") as source, retained_log.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
    else:
        retained_log.write_text(str(error)[:4096] + "\n", encoding="utf-8")
    if retained_log.stat().st_size > _MAX_LOG:
        retained_log.write_bytes(retained_log.read_bytes()[-_MAX_LOG:])
    retained_log.chmod(0o400)
    _atomic_json(
        failure / "failure.json",
        {
            "schema_version": "dwarf.library-target-failure.v1",
            "execution_id": execution_id,
            "cache_key": requirement["cache_key"],
            "target_id": requirement["target_id"],
            "source_revision": requirement["source_revision"],
            "recipe_digest": requirement["recipe_digest"],
            "error_type": type(error).__name__,
            "detail": str(error)[:4096],
            "failed_at": _now(),
            "build_log_sha256": _digest_file(retained_log),
        },
    )
    failure.chmod(0o500)


def _prepare_one(
    runtime,
    requirement: dict[str, Any],
    *,
    execution_id: str,
    deadline: float,
    cancel_event: threading.Event,
    builder: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    _validate_trusted_recipe_inputs(requirement)
    root = _safe_root(runtime)
    for name in ("cache", "locks", "staging", "failures"):
        path = root / name
        if path.is_symlink():
            raise LibraryTargetBuildError("library target cache path cannot be a symlink")
        path.mkdir(exist_ok=True, mode=0o700)
    key = requirement["cache_key"].removeprefix("sha256:")
    destination = root / "cache" / key
    lock_path = root / "locks" / f"{key}.lock"
    lock = _acquire_lock(lock_path, deadline=deadline, cancel_event=cancel_event)
    try:
        if destination.exists():
            return {
                **_validated_cache(
                    destination, requirement, str(runtime.installation_id)
                ),
                "cache_status": "verified-hit",
            }
        stage = root / "staging" / f"{execution_id}-{key[:16]}-{uuid.uuid4().hex}"
        stage.mkdir(mode=0o700)
        log_path = stage / "build.log"
        failure_log_path = log_path
        publish = None
        try:
            metadata = builder(
                requirement,
                stage,
                log_path,
                runtime=runtime,
                execution_id=execution_id,
                deadline=deadline,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                raise LibraryTargetBuildError(
                    "library target preparation was cancelled"
                )
            if not isinstance(metadata, dict):
                raise LibraryTargetBuildError("library target builder returned no identity")
            binary = stage / "executable"
            _regular(binary, maximum=_MAX_BINARY, label="built binary")
            _regular(log_path, maximum=_MAX_LOG, label="build log")
            if not os.access(binary, os.X_OK):
                raise LibraryTargetBuildError("built library target is not executable")
            source_commit = str(metadata.get("source_commit") or "")
            source_tree = str(metadata.get("source_tree") or "")
            if source_commit != requirement["source_revision"]:
                raise LibraryTargetBuildError(
                    "built library target source identity differs"
                )
            if not _REVISION.fullmatch(source_tree):
                raise LibraryTargetBuildError(
                    "built library target source tree is invalid"
                )
            effective_lock = stage / "effective-Cargo.lock"
            upstream_lock = stage / "upstream-Cargo.lock"
            _regular(
                upstream_lock,
                maximum=_MAX_LOG,
                label="upstream Cargo lock",
            )
            _regular(
                effective_lock,
                maximum=_MAX_LOG,
                label="effective Cargo lock",
            )
            selected_toolchain = str(metadata.get("selected_toolchain") or "")
            rustc_version = str(metadata.get("rustc_version") or "")
            cargo_version = str(metadata.get("cargo_version") or "")
            upstream_lock_sha256 = str(metadata.get("upstream_lock_sha256") or "")
            effective_lock_sha256 = str(metadata.get("effective_lock_sha256") or "")
            build_command = metadata.get("build_command")
            if (
                not selected_toolchain
                or not rustc_version
                or not cargo_version
                or upstream_lock_sha256 != _digest_file(upstream_lock)
                or effective_lock_sha256 != _digest_file(effective_lock)
                or build_command != _expected_build_command(selected_toolchain)
            ):
                raise LibraryTargetBuildError(
                    "built library target toolchain or lock identity is invalid"
                )
            receipt = {
                "schema_version": "dwarf.library-target-build.v1",
                "cache_key": requirement["cache_key"],
                "target_id": requirement["target_id"],
                "implementation": requirement["implementation"],
                "version": requirement["version"],
                "source_repository": requirement["source_repository"],
                "source_revision": requirement["source_revision"],
                "source_commit": source_commit,
                "source_tree": source_tree,
                "platform": requirement["platform"],
                "recipe_digest": requirement["recipe_digest"],
                "builder_image": requirement["builder_image"],
                "builder_transform": requirement["builder_transform"],
                "toolchain": requirement["toolchain"],
                "selected_toolchain": selected_toolchain,
                "rustc_version": rustc_version,
                "cargo_version": cargo_version,
                "upstream_lock_sha256": upstream_lock_sha256,
                "effective_lock_sha256": effective_lock_sha256,
                "controller_installation_id": str(runtime.installation_id),
                "requirement_digest": _digest_bytes(
                    _canonical(_artifact_requirement(requirement))
                ),
                "binary_sha256": _digest_file(binary),
                "binary_size": binary.stat().st_size,
                "build_log_sha256": _digest_file(log_path),
                "build_command": build_command,
                "created_at": _now(),
            }
            publish = root / "staging" / (
                f"publish-{execution_id}-{key[:16]}-{uuid.uuid4().hex}"
            )
            publish.mkdir(mode=0o700)
            shutil.copyfile(binary, publish / "executable")
            shutil.copyfile(log_path, publish / "build.log")
            failure_log_path = publish / "build.log"
            shutil.copyfile(upstream_lock, publish / "upstream-Cargo.lock")
            shutil.copyfile(effective_lock, publish / "effective-Cargo.lock")
            (publish / "executable").chmod(0o500)
            (publish / "build.log").chmod(0o400)
            (publish / "upstream-Cargo.lock").chmod(0o400)
            (publish / "effective-Cargo.lock").chmod(0o400)
            _atomic_json(publish / "receipt.json", receipt)
            if cancel_event.is_set():
                raise LibraryTargetBuildError(
                    "library target preparation was cancelled"
                )
            shutil.rmtree(stage)
            os.replace(publish, destination)
            publish = None
            failure_log_path = destination / "build.log"
            destination.chmod(0o500)
        except BaseException as exc:
            try:
                _retain_failure(
                    root,
                    execution_id,
                    requirement,
                    failure_log_path,
                    exc,
                )
            except (OSError, ValueError):
                pass
            if publish is not None:
                shutil.rmtree(publish, ignore_errors=True)
            elif destination.exists() and failure_log_path.parent == destination:
                destination.chmod(0o700)
                shutil.rmtree(destination, ignore_errors=True)
            shutil.rmtree(stage, ignore_errors=True)
            if isinstance(exc, LibraryTargetBuildError):
                raise
            raise LibraryTargetBuildError(str(exc)) from exc
        return {
            **_validated_cache(destination, requirement, str(runtime.installation_id)),
            "cache_status": "built",
        }
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def prepare_library_targets(
    runtime,
    requirements: list[dict[str, Any]],
    *,
    execution_id: str,
    timeout: int,
    cancel_event: threading.Event,
    builder: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build or validate every exact target requirement on the controller host."""

    if not isinstance(execution_id, str) or not _EXECUTION.fullmatch(execution_id):
        raise LibraryTargetBuildError("library target execution id is invalid")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise LibraryTargetBuildError("library target timeout is invalid")
    if not isinstance(cancel_event, threading.Event):
        raise LibraryTargetBuildError("library target cancellation handle is invalid")
    if not isinstance(requirements, list) or not requirements:
        raise LibraryTargetBuildError("library target requirements are missing")
    checked = [_validate_requirement(item) for item in requirements]
    deadline = time.monotonic() + timeout
    selected_builder = builder or _default_builder
    results = []
    by_key: dict[str, dict[str, Any]] = {}
    for requirement in checked:
        if requirement["cache_key"] not in by_key:
            by_key[requirement["cache_key"]] = _prepare_one(
                runtime,
                requirement,
                execution_id=execution_id,
                deadline=deadline,
                cancel_event=cancel_event,
                builder=selected_builder,
            )
        results.append(
            {
                **by_key[requirement["cache_key"]],
                "location": requirement["location"],
            }
        )
    return results
