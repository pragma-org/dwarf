#!/usr/bin/env python3
"""Run one bounded real Cardano-node measurement workload leg."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.runtime_amaru_measurement_calibration import (
    ACCEPTANCE_CASE_SET,
    CLIENT_INVALID_CASE_SET,
    DEFAULT_CASE_SET,
    VERSION_TABLE_FORWARD_COMPAT_CASE_SET,
    HANDSHAKE_UNSUPPORTED_VERSION_HEX,
    RESPONSE_CAP_BYTES,
    build_attempt_record,
    build_case_plan,
    run_attempts,
    summarize_attempts,
)
from profile_manager.measurement_collectors.cardano_patched import (
    CARDANO_MEASUREMENT_PATCH_SHA256,
    CARDANO_MEASUREMENT_REVISIONS,
    CARDANO_SOURCE_REVISION,
    paired_cardano_overhead_calibration,
)
from scripts.qualify_node_versions import classify_log_signals


SEED = "0xCA4DA001"
PLUTUS_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "corpora"
    / "cardano-measurement"
    / "always-succeeds-v2.plutus"
)
PLUTUS_FAIL_SCRIPT = PLUTUS_SCRIPT.with_name("always-fails-v2.plutus")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _physical_memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def build_workload_identity(
    *, attempt_count: int, seed: str = SEED,
    case_set: str = DEFAULT_CASE_SET,
    plutus_transactions: int = 0, epoch_observation_seconds: float = 0,
) -> dict[str, Any]:
    plan = build_case_plan(attempt_count=attempt_count, case_set=case_set)
    cases = []
    for name in dict.fromkeys(row["name"] for row in plan):
        row = next(item for item in plan if item["name"] == name)
        cases.append(
            {
                "name": name,
                "payload_hex": row["payload_hex"],
                "attempt_count": sum(item["name"] == name for item in plan),
            }
        )
    body = {
        "scenario_id": "cardano-measurement-calibration",
        "implementation": "cardano-node",
        "seed": seed,
        "attempt_count": int(attempt_count),
        "mini_protocol": "handshake",
        "case": "unsupported-version-refusal",
        "case_set": case_set,
        "cases": cases,
        "payload_hex": HANDSHAKE_UNSUPPORTED_VERSION_HEX,
        "network_magic": 42,
        "transport": "tcp",
        "target_port": 3001,
        "mux_direction_bit": 0,
        "response_cap_bytes": RESPONSE_CAP_BYTES,
        "plutus_transactions": int(plutus_transactions),
        "epoch_observation_seconds": float(epoch_observation_seconds),
        "plutus_script_sha256": (
            _sha256_bytes(PLUTUS_SCRIPT.read_bytes())
            if plutus_transactions
            else None
        ),
        "plutus_fail_script_sha256": (
            _sha256_bytes(PLUTUS_FAIL_SCRIPT.read_bytes())
            if plutus_transactions > 1
            else None
        ),
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {**body, "workload_digest": _sha256_bytes(encoded)}


def _result_context(output_dir: Path) -> dict[str, Any]:
    system = platform.uname()
    script = Path(__file__).resolve()
    return {
        "run_id": output_dir.name,
        "timing_policy": {
            "clock": "monotonic-perf-counter-ns",
            "start": "before-tcp-connect",
            "stop": "terminal-response-eof-timeout-or-disconnect",
            "warmup": "none",
            "outcome_inclusion": "all",
        },
        "hardware": {
            "system": system.system,
            "kernel_release": system.release,
            "architecture": system.machine,
            "logical_cpu_count": os.cpu_count(),
            "physical_memory_bytes": _physical_memory_bytes(),
        },
        "runner": {"script": script.name, "script_sha256": _sha256_bytes(script.read_bytes())},
    }


def _run(command: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)


def _checked(command: list[str], *, timeout: float = 60) -> subprocess.CompletedProcess:
    result = _run(command, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or f"command failed: {command[1]}"
        )
    return result


def _transaction_id(cli: str, transaction: Path) -> str:
    result = _checked(
        [cli, "conway", "transaction", "txid", "--tx-file", str(transaction)]
    )
    text = result.stdout.strip()
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return text
    return str(body.get("txhash") or "")


def _query_utxo(
    *, cli: str, socket_path: str, network_magic: int,
    address: str, destination: Path,
) -> dict[str, Any]:
    _checked([
        cli, "query", "utxo", "--address", address,
        "--socket-path", socket_path, "--testnet-magic", str(network_magic),
        "--out-file", str(destination),
    ])
    return json.loads(destination.read_text(encoding="utf-8"))


def _wait_for_utxo(
    *, cli: str, socket_path: str, network_magic: int, address: str,
    destination: Path, transaction_id: str, present: bool, timeout_seconds: float = 60,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter_ns()
    deadline = time.monotonic() + timeout_seconds
    while True:
        body = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=address, destination=destination,
        )
        observed = any(key.startswith(transaction_id + "#") for key in body)
        if observed is present:
            return body, (time.perf_counter_ns() - started) / 1_000
        if time.monotonic() >= deadline:
            state = "appear" if present else "be spent"
            raise RuntimeError(f"transaction {transaction_id} did not {state} before timeout")
        time.sleep(0.5)


def _wait_for_utxo_key_absent(
    *, cli: str, socket_path: str, network_magic: int, address: str,
    destination: Path, utxo_key: str, timeout_seconds: float = 60,
) -> float:
    started = time.perf_counter_ns()
    deadline = time.monotonic() + timeout_seconds
    while True:
        body = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=address, destination=destination,
        )
        if utxo_key not in body:
            return (time.perf_counter_ns() - started) / 1_000
        if time.monotonic() >= deadline:
            raise RuntimeError(f"collateral {utxo_key} was not consumed before timeout")
        time.sleep(0.5)


def _inspect_container(identifier: str) -> dict[str, Any]:
    if not identifier:
        raise RuntimeError("container identity is unavailable")
    result = _run(["docker", "inspect", identifier])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot inspect container {identifier}")
    try:
        inspected = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("container inspect result is invalid") from exc
    if not isinstance(inspected, dict):
        raise RuntimeError("container inspect result is invalid")
    return inspected


def _controller_container() -> dict[str, Any] | None:
    """Return the installed controller identity, or None for direct host mode."""
    explicit_identifier = str(
        os.environ.get("DWARF_CONTROLLER_CONTAINER_ID") or ""
    )
    identifier = explicit_identifier or str(os.environ.get("HOSTNAME") or "")
    controller_mode = (
        str(os.environ.get("ADA2_DWARF_RUNS_DIR") or "") == "/var/dwarf/runs"
    )
    if not identifier:
        if controller_mode:
            raise RuntimeError("installed controller has no container identity")
        return None
    try:
        result = _run(["docker", "inspect", identifier])
    except (OSError, subprocess.SubprocessError) as exc:
        if controller_mode:
            raise RuntimeError("cannot inspect the installed DWARF controller") from exc
        return None
    if result.returncode != 0:
        if controller_mode:
            raise RuntimeError("cannot inspect the installed DWARF controller")
        return None
    try:
        inspected = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("controller container inspect result is invalid") from exc
    if not isinstance(inspected, dict):
        raise RuntimeError("controller container inspect result is invalid")
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    if (
        labels.get("io.dwarf.application") != "true"
        or labels.get("io.dwarf.component") != "controller"
    ):
        raise RuntimeError("container does not identify the installed DWARF controller")
    inspected_id = str(inspected.get("Id") or "")
    inspected_name = str(inspected.get("Name") or "").removeprefix("/")
    identity_matches = inspected_id.startswith(identifier)
    if explicit_identifier:
        identity_matches = identity_matches or inspected_name == identifier
    if not identity_matches:
        raise RuntimeError("controller container identity does not match its identifier")
    return inspected


def _mount_source(inspected: dict[str, Any], destination: str, *, writable: bool) -> str:
    matches = [
        mount for mount in inspected.get("Mounts") or []
        if mount.get("Destination") == destination
    ]
    if len(matches) != 1:
        raise RuntimeError(f"container has no unique {destination} mount")
    mount = matches[0]
    source = str(mount.get("Source") or "")
    if not Path(source).is_absolute() or (writable and mount.get("RW") is not True):
        raise RuntimeError(f"container {destination} mount contract is invalid")
    return source


def _verify_workload_container(
    runtime: dict[str, Any], node: dict[str, Any]
) -> dict[str, Any]:
    container = str(node.get("container_name") or "")
    expected_container_id = str(node.get("container_id") or "")
    if not expected_container_id:
        raise RuntimeError("Cardano workload target has no immutable container identity")
    inspected = _inspect_container(expected_container_id)
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    expected_labels = {
        "ada2.managed": "dwarf",
        "io.dwarf.managed": "true",
        "io.dwarf.component": "node",
        "io.dwarf.implementation": "cardano-node",
        "io.dwarf.topology": str(runtime.get("profile_id") or ""),
        "io.dwarf.node_id": str(node.get("id") or ""),
        "com.docker.compose.project": str(runtime.get("compose_project") or ""),
        "com.docker.compose.service": str(node.get("service_name") or node.get("id") or ""),
    }
    if any(not value or labels.get(key) != value for key, value in expected_labels.items()):
        raise RuntimeError("Cardano workload target ownership labels do not match")
    expected_ref = str(node.get("image_ref") or "")
    expected_id = str(
        node.get("image_id")
        or node.get("container_image_id")
        or (node.get("artifact_identity") or {}).get("expected_image_id")
        or ""
    )
    if not expected_ref or not expected_id:
        raise RuntimeError("Cardano workload target image identity is incomplete")
    config = inspected.get("Config") or {}
    if config.get("Image") != expected_ref or inspected.get("Image") != expected_id:
        raise RuntimeError("Cardano workload target image identity does not match")
    if inspected.get("Id") != expected_container_id:
        raise RuntimeError("Cardano workload target container identity does not match")
    names = {str(inspected.get("Name") or "").lstrip("/")}
    names.update(str(name).lstrip("/") for name in inspected.get("Names") or [])
    if container not in names:
        raise RuntimeError("Cardano workload target container name does not match identity")
    if (inspected.get("State") or {}).get("Running") is not True:
        raise RuntimeError("Cardano workload target is not running")
    return inspected


def _owned_socket_volume(
    *, inspected_target: dict[str, Any], controller: dict[str, Any],
    runtime: dict[str, Any], node: dict[str, Any], destination: str,
) -> tuple[str, str]:
    service = str(node.get("service_name") or "")
    volume_key = str(node.get("socket_volume_key") or "")
    if not service or not volume_key:
        raise RuntimeError("Cardano workload socket volume identity is incomplete")
    matches = [
        mount for mount in inspected_target.get("Mounts") or []
        if mount.get("Destination") == destination
    ]
    if len(matches) != 1:
        raise RuntimeError("Cardano workload target has no unique socket volume")
    mount = matches[0]
    volume_name = str(mount.get("Name") or "")
    if mount.get("Type") != "volume" or not volume_name:
        raise RuntimeError("Cardano workload socket is not on a named volume")
    inspected = _run(["docker", "volume", "inspect", volume_name])
    try:
        volumes = json.loads(inspected.stdout) if inspected.returncode == 0 else []
    except json.JSONDecodeError as exc:
        raise RuntimeError("Cardano workload socket volume inspect is invalid") from exc
    if len(volumes) != 1 or not isinstance(volumes[0], dict):
        raise RuntimeError("Cardano workload socket volume inspect is invalid")
    labels = volumes[0].get("Labels") or {}
    controller_labels = (controller.get("Config") or {}).get("Labels") or {}
    expected_labels = {
        "io.dwarf.managed": "true",
        "io.dwarf.component": "volume",
        "io.dwarf.installation": str(controller_labels.get("io.dwarf.installation") or ""),
        "io.dwarf.topology": str(runtime.get("profile_id") or ""),
        "com.docker.compose.project": str(runtime.get("compose_project") or ""),
        "com.docker.compose.volume": volume_key,
    }
    if (
        volumes[0].get("Name") != volume_name
        or any(not value or labels.get(key) != value for key, value in expected_labels.items())
    ):
        raise RuntimeError("Cardano workload socket volume ownership is invalid")
    target_user = str((inspected_target.get("Config") or {}).get("User") or "")
    if target_user in {"", "0", "0:0"}:
        workload_user = "0:0"
        uid, gid = 0, 0
    else:
        match = re.fullmatch(r"(\d+):(\d+)", target_user)
        if match is None:
            raise RuntimeError("Cardano workload target user identity is not numeric")
        uid, gid = int(match.group(1)), int(match.group(2))
        workload_user = f"{uid}:{gid}"
    socket_path = str(node.get("container_socket_path") or "")
    target_id = str(inspected_target.get("Id") or "")
    if target_id != str(node.get("container_id") or ""):
        raise RuntimeError("Cardano workload target container identity changed")
    stat = _run(
        ["docker", "exec", target_id,
         "stat", "-Lc", "%u:%g:%a:%F", socket_path]
    )
    match = re.fullmatch(r"(\d+):(\d+):([0-7]{3,4}):socket\s*", stat.stdout) \
        if stat.returncode == 0 else None
    if match is None:
        raise RuntimeError("Cardano workload target socket identity is invalid")
    socket_uid, socket_gid, mode = int(match.group(1)), int(match.group(2)), int(match.group(3), 8)
    writable = (
        uid == 0
        or (uid == socket_uid and bool(mode & 0o200))
        or (gid == socket_gid and bool(mode & 0o020))
        or bool(mode & 0o002)
    )
    if not writable:
        raise RuntimeError("Cardano workload target user cannot access the socket")
    return volume_name, workload_user


def _cleanup_workload_containers(workload_id: str) -> None:
    label = f"io.dwarf.workload={workload_id}"
    listed = _run(["docker", "ps", "-aq", "--filter", f"label={label}"])
    if listed.returncode != 0:
        raise RuntimeError("cannot enumerate owned Cardano workload containers")
    container_ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    for container_id in container_ids:
        if not (12 <= len(container_id) <= 64):
            raise RuntimeError("owned Cardano workload container identity is invalid")
        try:
            int(container_id, 16)
        except ValueError as exc:
            raise RuntimeError("owned Cardano workload container identity is invalid") from exc
        inspected = _inspect_container(container_id)
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        if (
            labels.get("io.dwarf.managed") != "true"
            or labels.get("io.dwarf.component") != "cardano-cli-workload"
            or labels.get("io.dwarf.workload") != workload_id
        ):
            raise RuntimeError("refusing to remove a foreign Cardano workload container")
    if container_ids:
        removed = _run(["docker", "rm", "-f", *container_ids], timeout=30)
        if removed.returncode != 0:
            raise RuntimeError("cannot remove owned Cardano workload containers")


def _owned_run_output_relative(
    output_dir: Path, *, runs_root: Path = Path("/var/dwarf/runs")
) -> tuple[Path, Path]:
    """Return a real, non-symlinked output path beneath the owned runs root."""
    output_absolute = Path(os.path.abspath(output_dir))
    try:
        runs_resolved = runs_root.resolve(strict=True)
        output_resolved = output_absolute.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Cardano workload output directory is unavailable") from exc
    if output_resolved != output_absolute:
        raise RuntimeError("Cardano workload output directory is symlinked")
    try:
        output_relative = output_resolved.relative_to(runs_resolved)
    except ValueError as exc:
        raise RuntimeError(
            "Cardano workload output is outside the retained runs root"
        ) from exc
    if not output_relative.parts:
        raise RuntimeError("Cardano workload output cannot be the runs root")
    return output_absolute, output_relative


@contextmanager
def _containerized_cardano_workload(
    *, runtime_root: Path, runtime: dict[str, Any], node: dict[str, Any],
    output_dir: Path, controller: dict[str, Any],
):
    """Run cardano-cli beside the target so its Unix socket path stays short."""
    container = str(node.get("container_name") or "")
    image = str(node.get("image_ref") or "")
    socket_path = str(node.get("container_socket_path") or "")
    if not container or not socket_path:
        raise RuntimeError("Cardano-node workload has no container socket identity")
    if "@sha256:" not in image:
        raise RuntimeError("Cardano-node workload image is not digest-pinned")
    inspected_target = _verify_workload_container(runtime, node)
    socket_mount_destination = str(node.get("socket_mount_destination") or "/env")
    socket_mount_path = PurePosixPath(socket_mount_destination)
    socket_path_value = PurePosixPath(socket_path)
    if (
        not socket_mount_path.is_absolute()
        or socket_mount_path == PurePosixPath("/")
        or ".." in socket_mount_path.parts
        or not socket_path_value.is_absolute()
        or ".." in socket_path_value.parts
    ):
        raise RuntimeError("Cardano workload socket mount path is invalid")
    try:
        socket_path_value.relative_to(socket_mount_path)
    except ValueError as exc:
        raise RuntimeError("Cardano workload socket is outside its mounted volume") from exc
    if node.get("socket_mount_type") == "volume":
        socket_volume, workload_user = _owned_socket_volume(
            inspected_target=inspected_target,
            controller=controller,
            runtime=runtime,
            node=node,
            destination=socket_mount_destination,
        )
        socket_mount_lines = (
            f'--mount "type=volume,src={shlex.quote(socket_volume)},'
            f'dst={shlex.quote(socket_mount_destination)},readonly" '
        )
        target_source_line = (
            'OWNER_UID="$(id -u)"\n'
            'OWNER_GID="$(id -g)"\n'
        )
        user_line = f'--user "{workload_user}" '
        run_prefix = "if docker run "
        pre_run_lines = (
            "restore_work_owner() {\n"
            "  docker run --rm --user 0:0 --read-only "
            "--mount \"type=bind,src=$HOST_WORK_SOURCE,dst=/work\" "
            "--entrypoint chown \"$IMAGE\" -R \"$OWNER_UID:$OWNER_GID\" /work\n"
            "}\n"
            "trap restore_work_owner EXIT\n"
        )
        run_suffix = (
            "; then\n"
            "  status=0\n"
            "else\n"
            "  status=$?\n"
            "fi\n"
            "if restore_work_owner; then\n"
            "  restore_status=0\n"
            "else\n"
            "  restore_status=$?\n"
            "fi\n"
            "trap - EXIT\n"
            "if [ \"$restore_status\" -ne 0 ]; then\n"
            "  exit \"$restore_status\"\n"
            "fi\n"
            "exit \"$status\"\n"
        )
    else:
        target_env_source = _mount_source(
            inspected_target, socket_mount_destination, writable=False
        )
        socket_mount_lines = (
            '--mount "type=bind,src=$TARGET_ENV_SOURCE,'
            f'dst={shlex.quote(socket_mount_destination)},readonly" '
        )
        target_source_line = f"TARGET_ENV_SOURCE={shlex.quote(target_env_source)}\n"
        user_line = '--user "$(id -u):$(id -g)" '
        run_prefix = "exec docker run "
        pre_run_lines = ""
        run_suffix = "\n"
    runs_root = Path("/var/dwarf/runs")
    output_absolute, output_relative = _owned_run_output_relative(
        output_dir, runs_root=runs_root
    )
    runs_source = Path(
        _mount_source(controller, str(runs_root), writable=True)
    )
    host_work_source = runs_source / output_relative
    declared_key_root = Path(
        runtime.get("workload_key_root")
        or runtime_root / "env" / "utxo-keys" / "utxo1"
    )
    if declared_key_root.is_absolute() and declared_key_root.parts[:2] == ("/", "env"):
        key_root = (runtime_root / "env" / declared_key_root.relative_to("/env")).resolve()
    else:
        key_root = declared_key_root.resolve()
    if not all((key_root / name).is_file() for name in ("utxo.vkey", "utxo.skey")):
        raise RuntimeError("Cardano-node measurement workload has no genesis UTxO keys")
    env_root = (runtime_root / "env").resolve()
    try:
        key_relative = key_root.relative_to(env_root)
        container_key_root = Path("/env") / key_relative
    except ValueError:
        try:
            key_relative = key_root.relative_to(output_absolute)
        except ValueError as exc:
            raise RuntimeError(
                "Cardano workload keys are outside the owned runtime env or run output"
            ) from exc
        container_key_root = Path("/work") / key_relative
    workload_id = uuid.uuid4().hex
    primary_error: BaseException | None = None
    with tempfile.TemporaryDirectory(
        prefix=".dwarf-cardano-cli-", dir=output_absolute
    ) as temporary:
        wrapper = Path(temporary) / "cardano-cli"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"HOST_WORK={shlex.quote(str(output_absolute))}\n"
            f"HOST_WORK_SOURCE={shlex.quote(str(host_work_source))}\n"
            f"HOST_KEYS={shlex.quote(str(key_root))}\n"
            f"CONTAINER_KEYS={shlex.quote(str(container_key_root))}\n"
            f"{target_source_line}"
            f"IMAGE={shlex.quote(image)}\n"
            f"{pre_run_lines}"
            f"WORKLOAD_ID={shlex.quote(workload_id)}\n"
            "NAME=\"dwarf-cardano-cli-${WORKLOAD_ID}-$$\"\n"
            "args=()\n"
            "for arg in \"$@\"; do\n"
            "  arg=$(printf '%s' \"$arg\" | sed "
            "\"s|$HOST_WORK|/work|;s|$HOST_KEYS|$CONTAINER_KEYS|\")\n"
            "  args+=(\"$arg\")\n"
            "done\n"
            f"{run_prefix}--rm --name \"$NAME\" "
            "--label io.dwarf.managed=true "
            "--label io.dwarf.component=cardano-cli-workload "
            "--label \"io.dwarf.workload=$WORKLOAD_ID\" "
            f"{user_line}"
            "--mount \"type=bind,src=$HOST_WORK_SOURCE,dst=/work\" "
            f"{socket_mount_lines}"
            "--entrypoint cardano-cli \"$IMAGE\" \"${args[@]}\""
            f"{run_suffix}",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        workload_runtime = dict(runtime)
        workload_runtime["controller_support_binaries"] = dict(
            runtime.get("support_binaries") or {}
        )
        workload_runtime["support_binaries"] = {"cardano-cli": str(wrapper)}
        workload_runtime["workload_key_root"] = str(key_root)
        workload_runtime["controller_workload_key_root"] = str(key_root)
        workload_runtime["container_workload_key_root"] = str(container_key_root)
        workload_runtime["workload_socket_remote"] = True
        workload_node = dict(node)
        workload_node["socket_path"] = socket_path
        workload_node["container_socket_path"] = socket_path
        try:
            yield workload_runtime, workload_node
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                _cleanup_workload_containers(workload_id)
            except Exception as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(f"Cardano workload cleanup failed: {cleanup_error}")


def run_plutus_workload(
    *, runtime_root: Path, runtime: dict[str, Any], node: dict[str, Any],
    output_dir: Path, transaction_count: int,
) -> dict[str, Any]:
    if transaction_count < 1 or transaction_count > 100:
        raise ValueError("plutus transaction count must be within 1..100")
    for source in (PLUTUS_SCRIPT, PLUTUS_FAIL_SCRIPT):
        if not source.is_file():
            raise RuntimeError(f"retained Plutus workload is missing: {source}")
    cli = str((runtime.get("support_binaries") or {}).get("cardano-cli") or "cardano-cli")
    socket_path = str(node.get("socket_path") or "")
    network_magic = runtime.get("network_magic")
    if (
        isinstance(network_magic, bool)
        or not isinstance(network_magic, int)
        or not 0 <= network_magic <= 2**32 - 1
    ):
        raise RuntimeError("Cardano-node measurement workload has no exact network magic")
    if not socket_path or (
        runtime.get("workload_socket_remote") is not True
        and not Path(socket_path).exists()
    ):
        raise RuntimeError("Cardano-node measurement workload has no live host socket")
    key_root = Path(runtime.get("workload_key_root") or runtime_root / "env" / "utxo-keys" / "utxo1")
    verification_key = key_root / str(runtime.get("workload_vkey_name") or "utxo.vkey")
    signing_key = key_root / str(runtime.get("workload_skey_name") or "utxo.skey")
    if not verification_key.is_file() or not signing_key.is_file():
        raise RuntimeError("Cardano-node measurement workload has no genesis UTxO keys")
    work = output_dir / "plutus-workload"
    work.mkdir(parents=True, exist_ok=True)
    scripts = {
        "accepted": work / "always-succeeds-v2.plutus",
        "rejected": work / "always-fails-v2.plutus",
    }
    shutil.copy2(PLUTUS_SCRIPT, scripts["accepted"])
    shutil.copy2(PLUTUS_FAIL_SCRIPT, scripts["rejected"])
    payment_address = _checked([
        cli, "address", "build", "--testnet-magic", str(network_magic),
        "--payment-verification-key-file", str(verification_key),
    ]).stdout.strip()
    script_addresses = {
        outcome: _checked([
            cli, "address", "build", "--payment-script-file", str(script),
            "--testnet-magic", str(network_magic),
        ]).stdout.strip()
        for outcome, script in scripts.items()
    }
    records = []
    for index in range(transaction_count):
        expected_outcome = "accepted" if index % 2 == 0 else "rejected"
        script = scripts[expected_outcome]
        script_address = script_addresses[expected_outcome]
        regular = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=payment_address, destination=work / f"regular-{index}-before.json",
        )
        if not regular:
            raise RuntimeError("measurement payment address has no spendable UTxO")
        tx_in = max(
            regular,
            key=lambda key: int((regular[key].get("value") or {}).get("lovelace", 0)),
        )
        lock_body = work / f"lock-{index}.body"
        lock_tx = work / f"lock-{index}.tx"
        _checked([
            cli, "conway", "transaction", "build",
            "--testnet-magic", str(network_magic), "--socket-path", socket_path,
            "--change-address", payment_address, "--tx-in", tx_in,
            "--tx-out", f"{script_address}+5000000",
            "--tx-out-inline-datum-value", "42",
            "--tx-out", f"{payment_address}+5000000",
            "--out-file", str(lock_body),
        ], timeout=120)
        _checked([
            cli, "conway", "transaction", "sign", "--tx-body-file", str(lock_body),
            "--testnet-magic", str(network_magic), "--signing-key-file", str(signing_key),
            "--out-file", str(lock_tx),
        ])
        lock_started = time.perf_counter_ns()
        _checked([
            cli, "conway", "transaction", "submit", "--socket-path", socket_path,
            "--testnet-magic", str(network_magic), "--tx-file", str(lock_tx),
        ])
        lock_submit_us = (time.perf_counter_ns() - lock_started) / 1_000
        lock_id = _transaction_id(cli, lock_tx)
        script_utxo, lock_include_us = _wait_for_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=script_address, destination=work / f"script-{index}.json",
            transaction_id=lock_id, present=True,
        )
        script_tx_in = next(
            key for key in script_utxo if key.startswith(lock_id + "#")
        )
        regular = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=payment_address, destination=work / f"regular-{index}-collateral.json",
        )
        collateral_candidates = [
            key for key, value in regular.items()
            if key.startswith(lock_id + "#")
            and int((value.get("value") or {}).get("lovelace", 0)) == 5_000_000
        ]
        if not collateral_candidates:
            raise RuntimeError("lock transaction did not create the bounded collateral output")
        collateral = collateral_candidates[0]
        spend_body = work / f"spend-{index}.body"
        spend_tx = work / f"spend-{index}.tx"
        build_started = time.perf_counter_ns()
        build_command = [
            cli, "conway", "transaction", "build",
            "--testnet-magic", str(network_magic), "--socket-path", socket_path,
            "--change-address", payment_address, "--tx-in", script_tx_in,
            "--tx-in-script-file", str(script), "--tx-in-inline-datum-present",
            "--tx-in-redeemer-value", "42", "--tx-in-collateral", collateral,
            "--tx-out", f"{payment_address}+3000000",
        ]
        if expected_outcome == "rejected":
            build_command.append("--script-invalid")
        build_command.extend(["--out-file", str(spend_body)])
        _checked(build_command, timeout=120)
        build_us = (time.perf_counter_ns() - build_started) / 1_000
        _checked([
            cli, "conway", "transaction", "sign", "--tx-body-file", str(spend_body),
            "--testnet-magic", str(network_magic), "--signing-key-file", str(signing_key),
            "--out-file", str(spend_tx),
        ])
        submit_started = time.perf_counter_ns()
        _checked([
            cli, "conway", "transaction", "submit", "--socket-path", socket_path,
            "--testnet-magic", str(network_magic), "--tx-file", str(spend_tx),
        ])
        submit_response_nanos = time.perf_counter_ns() - submit_started
        submit_us = submit_response_nanos / 1_000
        spend_id = _transaction_id(cli, spend_tx)
        if expected_outcome == "accepted":
            _body, include_us = _wait_for_utxo(
                cli=cli, socket_path=socket_path, network_magic=network_magic,
                address=payment_address, destination=work / f"regular-{index}-after.json",
                transaction_id=spend_id, present=True,
            )
            chain_outcome = "included-valid"
        else:
            include_us = _wait_for_utxo_key_absent(
                cli=cli, socket_path=socket_path, network_magic=network_magic,
                address=payment_address,
                destination=work / f"regular-{index}-after.json",
                utxo_key=collateral,
            )
            chain_outcome = "included-invalid"
        outcome_nanos = time.perf_counter_ns() - submit_started
        records.append({
            "attempt_id": f"plutus-{index:04d}",
            "outcome": expected_outcome,
            "chain_outcome": chain_outcome,
            "lock_transaction_id": lock_id,
            "spend_transaction_id": spend_id,
            "lock_submit_us": lock_submit_us,
            "lock_submit_to_include_us": lock_include_us,
            "plutus_build_us": build_us,
            "plutus_submit_us": submit_us,
            "plutus_submit_to_include_us": include_us,
            "transaction_bytes": spend_tx.stat().st_size,
            "elapsed_nanos": outcome_nanos,
            "elapsed_micros": outcome_nanos / 1_000,
            "submit_to_protocol_response_nanos": submit_response_nanos,
            "submit_to_protocol_response_micros": submit_response_nanos / 1_000,
            "submit_to_block_inclusion_nanos": outcome_nanos,
            "submit_to_block_inclusion_micros": outcome_nanos / 1_000,
            "submit_to_chain_adoption_nanos": outcome_nanos,
            "submit_to_chain_adoption_micros": outcome_nanos / 1_000,
        })
    result = {
        "scripts": {
            outcome: {
                "artifact": f"plutus-workload/{script.name}",
                "sha256": _sha256_bytes(script.read_bytes()),
            }
            for outcome, script in scripts.items()
        },
        "transaction_count": transaction_count,
        "records": records,
    }
    (work / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _container_ip(container: str) -> str:
    result = _run(["docker", "inspect", container])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot inspect {container}")
    body = json.loads(result.stdout)[0]
    for network in ((body.get("NetworkSettings") or {}).get("Networks") or {}).values():
        if network.get("IPAddress"):
            return str(network["IPAddress"])
    raise RuntimeError(f"container has no routable address: {container}")


def _release(runtime: dict[str, Any]) -> dict[str, Any]:
    snapshot = ((runtime.get("version_provenance") or {}).get("catalog_snapshot") or {})
    for release in snapshot.get("selected_releases") or []:
        if release.get("implementation") == "cardano-node":
            return release
    raise RuntimeError("runtime metadata has no exact Cardano-node release")


def _target(runtime: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    release = _release(runtime)
    node = next(
        (node for node in (runtime.get("nodes") or []) if node.get("impl") == "cardano-node"),
        None,
    )
    if node is None:
        raise RuntimeError("runtime metadata has no Cardano-node target")
    artifact = node.get("artifact_identity") or {}
    version = node.get("version_identity") or {}
    stock_digest = next(
        item["digest"]
        for item in release.get("artifacts") or []
        if item.get("kind") == "oci" and item.get("availability") == "available"
    )
    observed_digest = artifact.get("image_digest") or artifact.get("container_image_id")
    if artifact.get("satisfied") is not True or version.get("satisfied") is not True:
        raise RuntimeError("Cardano-node runtime identity is not proven")
    if node.get("source_revision") != release.get("source_revision"):
        raise RuntimeError("Cardano-node runtime identity does not match the catalog")
    mode = str(node.get("target_mode") or "stock")
    if mode == "patched":
        if release.get("source_revision") != CARDANO_SOURCE_REVISION:
            raise RuntimeError("Cardano-node patched source revision does not match")
        if str(node.get("patch_set_sha256") or "") not in CARDANO_MEASUREMENT_REVISIONS:
            raise RuntimeError("Cardano-node patched patch-set identity does not match")
        patched_digest = str(node.get("image_digest") or observed_digest or "")
        if observed_digest != patched_digest:
            raise RuntimeError("Cardano-node patched image identity does not match runtime")
        for field in (
            "executable_digest", "build_result_sha256", "runtime_probe_log_sha256"
        ):
            value = str(node.get(field) or "")
            if not value.startswith("sha256:") or len(value) != 71:
                raise RuntimeError(f"Cardano-node patched {field} is not immutable")
        if not str(node.get("image_ref") or "").endswith("@" + patched_digest):
            raise RuntimeError("Cardano-node patched image reference is not digest-pinned")
        return ({
            "implementation": "cardano-node",
            "version": release["version"],
            "source_revision": release["source_revision"],
            "mode": "patched",
            "patch_set_sha256": node["patch_set_sha256"],
            "image_digest": patched_digest,
            "image_reference": node.get("image_ref"),
            "executable_digest": node["executable_digest"],
            "build_result_sha256": node["build_result_sha256"],
            "runtime_probe_log_sha256": node["runtime_probe_log_sha256"],
        }, node)
    if mode != "stock":
        raise RuntimeError(f"unsupported Cardano-node target mode: {mode}")
    if observed_digest != stock_digest:
        raise RuntimeError("Cardano-node runtime identity does not match the catalog")
    if str(node.get("image_ref") or "") != str(artifact.get("image_ref") or ""):
        raise RuntimeError("Cardano-node runtime image reference does not match identity")
    if not str(node.get("image_ref") or "").endswith("@" + stock_digest):
        raise RuntimeError("Cardano-node runtime image reference is not digest-pinned")
    return ({
        "implementation": "cardano-node",
        "version": release["version"],
        "source_revision": release["source_revision"],
        "mode": "stock",
        "image_digest": stock_digest,
        "image_reference": node.get("image_ref"),
    }, node)


def _capture_logs(container: str, *, since: str, destination: Path) -> dict[str, Any]:
    result = _run(["docker", "logs", "--since", since, container], timeout=120)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = result.stdout + ("\n" + result.stderr if result.stderr else "")
    destination.write_text(body, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot capture logs from {container}")
    record_count = 0
    protocol_record_count = 0
    namespaces: set[str] = set()
    for line in body.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        record_count += 1
        namespace = record.get("ns")
        if isinstance(namespace, str):
            namespaces.add(namespace)
            if namespace.startswith(
                ("Net.Handshake.", "ChainSync.", "BlockFetch.", "TxSubmission.", "Mempool")
            ) or namespace.startswith("ChainDB."):
                protocol_record_count += 1
    return {
        "record_count": record_count,
        "protocol_record_count": protocol_record_count,
        "byte_count": len(body.encode("utf-8")),
        "namespaces": sorted(namespaces),
    }


def _container_state(container: str) -> dict[str, Any]:
    result = _run(["docker", "inspect", container])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot inspect {container}")
    body = json.loads(result.stdout)[0]
    state = body.get("State") or {}
    return {
        "running": state.get("Running") is True,
        "status": state.get("Status"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled") is True,
        "restart_count": int(body.get("RestartCount") or 0),
    }


def _query_cardano_tip(
    container: str, *, socket_path: str = "/state/node.socket", network_magic: int = 42
) -> dict[str, Any] | None:
    result = _run(
        [
            "docker", "exec", container, "cardano-cli", "query", "tip",
            "--socket-path", socket_path, "--testnet-magic", str(network_magic),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    height = body.get("block")
    if isinstance(height, bool) or not isinstance(height, int) or not body.get("hash"):
        return None
    return {"block_height": height, "hash": body.get("hash"), "slot": body.get("slot")}


def _independent_peer(
    runtime: dict[str, Any], target_node: dict[str, Any]
) -> tuple[str, str, str]:
    target_id = str(target_node.get("id") or target_node.get("name") or "")
    for node in runtime.get("nodes") or []:
        node_id = str(node.get("id") or node.get("name") or "")
        container = str(node.get("container_name") or "")
        if node_id and node_id != target_id and container:
            socket_path = str(
                node.get("container_socket_path") or f"/env/socket/{node_id}/sock"
            )
            return node_id, container, socket_path
    raise RuntimeError("Cardano runtime has no independent honest peer")


def _peer_observation(
    peer_id: str,
    container: str,
    network_magic: int,
    *,
    socket_path: str = "/state/node.socket",
) -> dict[str, Any]:
    tip = _query_cardano_tip(
        container, socket_path=socket_path, network_magic=network_magic
    )
    return {
        "observed_at_epoch_seconds": time.time(),
        "peer_id": peer_id,
        "container": container,
        "usable": tip is not None,
        "tip": tip,
    }


def run_leg(
    *,
    runtime_root: Path,
    output_dir: Path,
    attempt_count: int,
    timeout_seconds: float,
    case_set: str = DEFAULT_CASE_SET,
    observation_seconds: float = 2.0,
    trace_timeout_seconds: float = 20.0,
    plutus_transactions: int = 0,
    epoch_observation_seconds: float = 0,
    attempt_interval_seconds: float = 0.0,
) -> dict[str, Any]:
    runtime = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    target, node = _target(runtime)
    container = str(node.get("container_name") or "")
    if not container:
        raise RuntimeError("Cardano-node target has no container identity")
    output_dir.mkdir(parents=True, exist_ok=True)
    client_case = case_set == CLIENT_INVALID_CASE_SET
    state_before = _container_state(container) if client_case else None
    target_socket = str(node.get("container_socket_path") or "/state/node.socket")
    network_magic = int(runtime.get("network_magic", 42))
    tip_before = (
        _query_cardano_tip(
            container, socket_path=target_socket, network_magic=network_magic
        )
        if client_case
        else None
    )
    peer_id = None
    peer_container = None
    peer_socket = None
    peer_before = None
    peer_during = None
    if client_case:
        peer_id, peer_container, peer_socket = _independent_peer(runtime, node)
        peer_before = _peer_observation(
            peer_id, peer_container, network_magic, socket_path=peer_socket
        )

    def observe_midpoint() -> None:
        nonlocal peer_during
        if peer_id is not None and peer_container is not None:
            peer_during = _peer_observation(
                peer_id, peer_container, network_magic, socket_path=peer_socket
            )

    started_at = _utc_now()
    attempts = run_attempts(
        host=_container_ip(container),
        port=3001,
        attempt_count=attempt_count,
        timeout_seconds=timeout_seconds,
        case_set=case_set,
        midpoint_callback=(observe_midpoint if client_case else None),
        attempt_interval_seconds=attempt_interval_seconds,
    )
    peer_after = (
        _peer_observation(
            peer_id, peer_container, network_magic, socket_path=peer_socket
        )
        if client_case and peer_id is not None and peer_container is not None
        else None
    )
    attempts_path = output_dir / "attempts.ndjson"
    attempts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in attempts),
        encoding="utf-8",
    )
    if plutus_transactions:
        controller = _controller_container()
        if controller is None:
            plutus_workload = run_plutus_workload(
                runtime_root=runtime_root,
                runtime=runtime,
                node=node,
                output_dir=output_dir,
                transaction_count=plutus_transactions,
            )
        else:
            with _containerized_cardano_workload(
                runtime_root=runtime_root,
                runtime=runtime,
                node=node,
                output_dir=output_dir,
                controller=controller,
            ) as (workload_runtime, workload_node):
                plutus_workload = run_plutus_workload(
                    runtime_root=runtime_root,
                    runtime=workload_runtime,
                    node=workload_node,
                    output_dir=output_dir,
                    transaction_count=plutus_transactions,
                )
    else:
        plutus_workload = None
    if epoch_observation_seconds:
        time.sleep(epoch_observation_seconds)
    trace_path = output_dir / "raw" / "node1.ndjson"
    observation_started = time.monotonic()
    time.sleep(observation_seconds)
    while True:
        trace_evidence = _capture_logs(
            container, since=started_at, destination=trace_path
        )
        if trace_evidence["protocol_record_count"] > 0:
            break
        elapsed = time.monotonic() - observation_started
        if elapsed >= trace_timeout_seconds:
            break
        time.sleep(min(1.0, trace_timeout_seconds - elapsed))
    observed_seconds = time.monotonic() - observation_started
    state_after = _container_state(container) if client_case else None
    tip_after = (
        _query_cardano_tip(
            container, socket_path=target_socket, network_magic=network_magic
        )
        if client_case
        else None
    )
    trace_text = trace_path.read_text(encoding="utf-8")
    log_signals = classify_log_signals(trace_text) if client_case else None
    health_checks = (
        {
            "all_attempts_classified_as_expected": all(
                row.get("outcome") == row.get("expected_external_outcome")
                for row in attempts
            ),
            "target_running_before": (state_before or {}).get("running") is True,
            "target_running_after": (state_after or {}).get("running") is True,
            "target_not_oom_killed": (state_after or {}).get("oom_killed") is False,
            "target_restart_count_unchanged": (
                (state_before or {}).get("restart_count") is not None
                and (state_before or {}).get("restart_count")
                == (state_after or {}).get("restart_count")
            ),
            "honest_chain_progressed": (
                isinstance((tip_before or {}).get("block_height"), int)
                and isinstance((tip_after or {}).get("block_height"), int)
                and tip_after["block_height"] > tip_before["block_height"]
            ),
            "no_fatal_signals": not bool((log_signals or {}).get("fatal")),
        }
        if client_case
        else None
    )
    summarized = summarize_attempts(attempts)
    result = {
        "schema_version": 1,
        "created_at": _utc_now(),
        **_result_context(output_dir),
        "target": target,
        "workload_identity": build_workload_identity(
            attempt_count=attempt_count,
            case_set=case_set,
            plutus_transactions=plutus_transactions,
            epoch_observation_seconds=epoch_observation_seconds,
        ),
        "measurements": {"handshake_rejection_roundtrip": summarized["combined"]},
        "measurements_by_outcome": {"handshake_rejection_roundtrip": summarized["by_outcome"]},
        "attempts": {
            "total": len(attempts),
            "outcomes": {
                outcome: sum(row["outcome"] == outcome for row in attempts)
                for outcome in sorted({row["outcome"] for row in attempts})
            },
            "artifact": "attempts.ndjson",
            **(
                {
                    "unexpected_count": sum(
                        row.get("outcome") != row.get("expected_external_outcome")
                        for row in attempts
                    ),
                    "by_case": {
                        case: {
                            "total": sum(row.get("case") == case for row in attempts),
                            "outcomes": {
                                outcome: sum(
                                    row.get("case") == case and row.get("outcome") == outcome
                                    for row in attempts
                                )
                                for outcome in sorted(
                                    {
                                        str(row.get("outcome"))
                                        for row in attempts
                                        if row.get("case") == case
                                    }
                                )
                            },
                        }
                        for case in dict.fromkeys(row.get("case") for row in attempts)
                    },
                }
                if case_set == CLIENT_INVALID_CASE_SET
                else {}
            ),
        },
        "raw_trace": "raw/node1.ndjson",
        "plutus_workload": plutus_workload,
        "node_trace": {
            **trace_evidence,
            "minimum_observation_seconds": observation_seconds,
            "observation_timeout_seconds": trace_timeout_seconds,
            "observed_seconds": observed_seconds,
        },
        **(
            {
                "status": "available" if all((health_checks or {}).values()) else "unavailable",
                "target_health": {
                    "before": state_before,
                    "after": state_after,
                    "tip_before": tip_before,
                    "tip_after": tip_after,
                    "log_signals": log_signals,
                    "checks": health_checks,
                    "observer": {
                        "implementation": "cardano-node",
                        "container": container,
                        "started_at": started_at,
                        "network_magic": network_magic,
                        "socket_path": target_socket,
                    },
                },
                "peer_session": {
                    "peer_id": peer_id,
                    "before": peer_before,
                    "during": peer_during,
                    "after": peer_after,
                    "recovered_within_seconds": (
                        0.0
                        if (peer_during or {}).get("usable") is True
                        else (
                            max(
                                0.0,
                                float((peer_after or {}).get("observed_at_epoch_seconds") or 0)
                                - float((peer_during or {}).get("observed_at_epoch_seconds") or 0),
                            )
                            if (peer_after or {}).get("usable") is True and peer_during
                            else None
                        )
                    ),
                    "observer": {
                        "peer_id": peer_id,
                        "container": peer_container,
                        "network_magic": network_magic,
                        "socket_path": peer_socket,
                    },
                },
            }
            if client_case
            else {}
        ),
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    leg = sub.add_parser("leg")
    leg.add_argument("--runtime-root", type=Path, required=True)
    leg.add_argument("--output-dir", type=Path, required=True)
    leg.add_argument("--attempts", type=int, default=100)
    leg.add_argument("--timeout-seconds", type=float, default=2.0)
    leg.add_argument(
        "--case-set",
        choices=[
            DEFAULT_CASE_SET,
            ACCEPTANCE_CASE_SET,
            CLIENT_INVALID_CASE_SET,
            VERSION_TABLE_FORWARD_COMPAT_CASE_SET,
        ],
        default=DEFAULT_CASE_SET,
    )
    leg.add_argument("--observation-seconds", type=float, default=2.0)
    leg.add_argument("--trace-timeout-seconds", type=float, default=20.0)
    leg.add_argument("--plutus-transactions", type=int, default=0)
    leg.add_argument("--epoch-observation-seconds", type=float, default=0)
    leg.add_argument("--attempt-interval-seconds", type=float, default=0.0)
    pair = sub.add_parser("pair")
    pair.add_argument("--stock", type=Path, required=True)
    pair.add_argument("--patched", type=Path, required=True)
    pair.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "leg":
        if args.attempts < 30 or args.attempts > 10_000:
            raise SystemExit("--attempts must be within 30..10000")
        if args.observation_seconds < 0.5 or args.observation_seconds > 30:
            raise SystemExit("--observation-seconds must be within 0.5..30")
        if (
            args.trace_timeout_seconds < args.observation_seconds
            or args.trace_timeout_seconds > 120
        ):
            raise SystemExit(
                "--trace-timeout-seconds must be at least observation-seconds and at most 120"
            )
        if args.plutus_transactions < 0 or args.plutus_transactions > 100:
            raise SystemExit("--plutus-transactions must be within 0..100")
        if args.epoch_observation_seconds < 0 or args.epoch_observation_seconds > 120:
            raise SystemExit("--epoch-observation-seconds must be within 0..120")
        result = run_leg(
            runtime_root=args.runtime_root,
            output_dir=args.output_dir,
            attempt_count=args.attempts,
            timeout_seconds=args.timeout_seconds,
            case_set=args.case_set,
            observation_seconds=args.observation_seconds,
            trace_timeout_seconds=args.trace_timeout_seconds,
            plutus_transactions=args.plutus_transactions,
            epoch_observation_seconds=args.epoch_observation_seconds,
            attempt_interval_seconds=args.attempt_interval_seconds,
        )
        trace = result["node_trace"]
        status = trace["record_count"] > 0 and trace["protocol_record_count"] > 0
    else:
        stock = json.loads(args.stock.read_text(encoding="utf-8"))
        patched = json.loads(args.patched.read_text(encoding="utf-8"))
        result = paired_cardano_overhead_calibration(
            stock, patched, minimum_samples=30
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        status = result["status"] == "available"
    print(json.dumps(result, sort_keys=True))
    return 0 if status else 2


if __name__ == "__main__":
    raise SystemExit(main())
