"""Run a hosted security campaign inside its trusted controller boundary."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from ipaddress import IPv4Address
from pathlib import Path


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY_DIGEST = re.compile(
    r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+@sha256:[0-9a-f]{64}\Z"
)
_SOURCE_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_DWARF_ROOT = Path(__file__).resolve().parents[1]


def _campaign_main(argv: list[str]) -> int:
    from scripts.ci_security_campaign import main

    return main(argv)


def _wait_for_listener(host: str, port: int, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"dashboard did not listen on {host}:{port}")


def run_inside(args) -> int:
    """Serve retained run artifacts while executing inside the controller."""

    dashboard_output = args.directory / "dashboard-static"
    dashboard_output.mkdir(parents=True, exist_ok=True)
    dashboard_log = args.directory / "dashboard.log"
    command = [
        sys.executable,
        str(_DWARF_ROOT / "cardano-profile"),
        "dashboard",
        "serve",
        "--output-dir",
        "/var/dwarf/dashboard-static",
        "--bind",
        "0.0.0.0",
        "--port",
        "8787",
    ]
    with dashboard_log.open("ab") as log:
        dashboard = subprocess.Popen(
            command,
            cwd=_DWARF_ROOT.parent,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_listener("127.0.0.1", 8787)
            return _campaign_main(
                [
                    "--manifest",
                    str(args.manifest),
                    "--stock-manifest",
                    str(args.stock_manifest),
                    "--build-observations",
                    str(args.build_observations),
                    "--directory",
                    str(args.directory),
                    "--catalog",
                    str(args.catalog),
                    "--signing-key",
                    str(args.signing_key),
                ]
            )
        finally:
            dashboard.terminate()
            try:
                dashboard.wait(timeout=10)
            except subprocess.TimeoutExpired:
                dashboard.kill()
                dashboard.wait(timeout=10)


def validate_helper_identity(reference: str, inspected: dict) -> dict[str, str]:
    """Verify the immutable raw-submission helper before campaign execution."""

    if not _REPOSITORY_DIGEST.fullmatch(reference):
        raise ValueError("helper image must be an exact GHCR repository digest")
    if inspected.get("Os") != "linux" or inspected.get("Architecture") != "amd64":
        raise ValueError("helper image must be linux/amd64")
    if reference not in (inspected.get("RepoDigests") or []):
        raise ValueError("helper image repository digest was not observed")
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    if labels.get("io.dwarf.component") != "dwarf-localtx-submit-raw":
        raise ValueError("helper image component identity is invalid")
    revision = str(labels.get("org.opencontainers.image.revision") or "")
    if not _SOURCE_REVISION.fullmatch(revision):
        raise ValueError("helper image source revision is invalid")
    image_id = str(inspected.get("Id") or "")
    if not _DIGEST.fullmatch(image_id):
        raise ValueError("helper image config identity is invalid")
    return {
        "reference": reference,
        "image_id": image_id,
        "source_revision": revision,
    }


def validate_controller_identity(
    reference: str, *, expected_revision: str, inspected: dict
) -> dict[str, str]:
    """Verify that a local image ID is the just-built trusted controller."""

    if not _DIGEST.fullmatch(reference):
        raise ValueError("controller image must be an exact config digest")
    if not _SOURCE_REVISION.fullmatch(expected_revision):
        raise ValueError("expected controller revision is invalid")
    if inspected.get("Id") != reference:
        raise ValueError("controller config identity does not match")
    if inspected.get("Os") != "linux" or inspected.get("Architecture") != "amd64":
        raise ValueError("controller image must be linux/amd64")
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    if labels.get("io.dwarf.component.role") != "controller":
        raise ValueError("controller component identity is invalid")
    revision = str(labels.get("org.opencontainers.image.revision") or "")
    if revision != expected_revision:
        raise ValueError("controller image does not match the trusted revision")
    return {"image_id": reference, "source_revision": revision}


def bridge_gateway(inspected: dict) -> str:
    """Return the one Docker bridge IPv4 gateway visible to workload nodes."""

    gateways = []
    for row in (inspected.get("IPAM") or {}).get("Config") or []:
        value = str((row or {}).get("Gateway") or "")
        try:
            address = IPv4Address(value)
        except ValueError:
            continue
        if not address.is_unspecified:
            gateways.append(str(address))
    if len(set(gateways)) != 1:
        raise ValueError("Docker bridge must expose exactly one IPv4 gateway")
    return gateways[0]


def _run_command(command: list[str], *, check: bool = True):
    kwargs = {"text": True, "check": check}
    if check:
        kwargs["capture_output"] = True
    return subprocess.run(command, **kwargs)


def _inspect_one(command: list[str]) -> dict:
    result = _run_command(command)
    try:
        rows = json.loads(result.stdout)
        document = rows[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise ValueError(f"invalid Docker inspection for {' '.join(command[1:])}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"invalid Docker inspection for {' '.join(command[1:])}")
    return document


def run_outer(args) -> int:
    """Verify the hosted boundary, then enter the trusted controller image."""

    workspace = args.workspace.resolve()
    runtime = args.runtime_directory.resolve()
    signing_key = args.signing_key.resolve()
    docker_socket = args.docker_socket.resolve()
    if not workspace.is_dir():
        raise ValueError("campaign workspace is missing")
    if not signing_key.is_file():
        raise ValueError("workflow evidence signing key is missing")
    if not docker_socket.exists():
        raise ValueError("Docker socket is missing")
    runtime.mkdir(parents=True, exist_ok=True)
    for relative in (
        "runs",
        "runtime-state",
        "bundles",
        "launches",
        "run-ledger",
        "dashboard-static",
    ):
        (runtime / relative).mkdir(parents=True, exist_ok=True)
    host_lock = runtime / "security-campaign.lock"
    host_lock.touch(exist_ok=True)

    _run_command(["docker", "pull", args.helper_image])
    helper = validate_helper_identity(
        args.helper_image,
        _inspect_one(["docker", "image", "inspect", args.helper_image]),
    )
    controller = validate_controller_identity(
        args.controller_image,
        expected_revision=args.expected_controller_revision,
        inspected=_inspect_one(
            ["docker", "image", "inspect", args.controller_image]
        ),
    )
    gateway = bridge_gateway(
        _inspect_one(["docker", "network", "inspect", "bridge"])
    )
    identity = {
        "schema_version": "v1",
        "kind": "github-hosted-controller-boundary",
        "installation_id": args.installation_id,
        "controller": controller,
        "localtxsubmission_helper": helper,
        "artifact_gateway": gateway,
    }
    (runtime / "hosted-runtime.json").write_text(
        json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    command = build_controller_command(
        controller_image=args.controller_image,
        helper_image=args.helper_image,
        workspace=workspace,
        runtime_directory=runtime,
        signing_key=signing_key,
        host_lock=host_lock,
        docker_socket=docker_socket,
        docker_socket_gid=docker_socket.stat().st_gid,
        host_uid=os.getuid(),
        host_gid=os.getgid(),
        installation_id=args.installation_id,
        artifact_base_url=f"http://{gateway}:8787",
    )
    return int(_run_command(command, check=False).returncode)


def build_controller_command(
    *,
    controller_image: str,
    helper_image: str,
    workspace: Path,
    runtime_directory: Path,
    signing_key: Path,
    host_lock: Path,
    docker_socket: Path,
    docker_socket_gid: int,
    host_uid: int,
    host_gid: int,
    installation_id: str,
    artifact_base_url: str,
) -> list[str]:
    """Build the exact controller-shaped Docker invocation for one hosted job."""

    if not _DIGEST.fullmatch(controller_image):
        raise ValueError("controller image must be an observed config digest")
    if not _REPOSITORY_DIGEST.fullmatch(helper_image):
        raise ValueError("helper image must be an exact GHCR repository digest")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", installation_id):
        raise ValueError("installation identity is invalid")
    controller_name = f"dwarf-controller-{installation_id}"
    workspace = workspace.resolve()
    runtime_directory = runtime_directory.resolve()
    signing_key = signing_key.resolve()
    host_lock = host_lock.resolve()
    docker_socket = docker_socket.resolve()
    runs = runtime_directory / "runs"
    state = runtime_directory / "runtime-state"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        controller_name,
        "--network",
        "host",
        "--user",
        f"{host_uid}:{host_gid}",
        "--group-add",
        str(docker_socket_gid),
        "--label",
        "io.dwarf.application=true",
        "--label",
        "io.dwarf.component=controller",
        "--label",
        f"io.dwarf.installation={installation_id}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
        "--mount",
        f"type=bind,src={workspace},dst={workspace},readonly",
        "--mount",
        f"type=bind,src={runtime_directory},dst=/var/dwarf",
        "--mount",
        f"type=bind,src={runs},dst=/var/dwarf/runs",
        "--mount",
        f"type=bind,src={signing_key},dst=/run/workflow-evidence.pem,readonly",
        "--mount",
        f"type=bind,src={docker_socket},dst=/var/run/docker.sock",
        "--mount",
        f"type=bind,src={host_lock},dst=/tmp/dwarf-security-campaign.lock",
    ]
    for value in (
        "HOME=/tmp",
        "DOCKER_CONFIG=/tmp/dwarf-docker-config",
        "PYTHONPATH=/home/dwarf/dwarf-fw/dwarf",
        "ADA2_DWARF_RUNS_DIR=/var/dwarf/runs",
        "ADA2_DWARF_STATE_DIR=/var/dwarf/runtime-state",
        "ADA2_DWARF_BUNDLES_DIR=/var/dwarf/bundles",
        "ADA2_DWARF_LAUNCH_ROOT=/var/dwarf/launches",
        "ADA2_DWARF_RUN_LEDGER_ROOT=/var/dwarf/run-ledger",
        f"DWARF_CONTROLLER_HOST_STATE_DIR={state}",
        f"DWARF_CONTROLLER_CONTAINER_ID={controller_name}",
        f"DWARF_INSTALLATION_ID={installation_id}",
        "DWARF_HOST_OS=linux",
        "DWARF_HOST_ARCH=amd64",
        f"DWARF_LOCALTXSUBMISSION_HELPER_IMAGE={helper_image}",
        f"DWARF_RUN_ARTIFACT_BASE_URL={artifact_base_url}",
    ):
        command.extend(("--env", value))
    command.extend(
        (
            "--entrypoint",
            "python3",
            controller_image,
            "-m",
            "scripts.ci_hosted_campaign_runtime",
            "inside",
            "--manifest",
            str(workspace / "campaign-plan/manifest.json"),
            "--stock-manifest",
            str(workspace / "campaign-plan/stock-manifest.json"),
            "--build-observations",
            str(workspace / "campaign-plan/build-observations.json"),
            "--directory",
            "/var/dwarf",
            "--catalog",
            str(workspace / "campaign-plan/version-catalog.json"),
            "--signing-key",
            "/run/workflow-evidence.pem",
        )
    )
    return command


def _add_campaign_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--stock-manifest", type=Path, required=True)
    parser.add_argument("--build-observations", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    inside = subparsers.add_parser("inside")
    _add_campaign_arguments(inside)
    outer = subparsers.add_parser("outer")
    outer.add_argument("--controller-image", required=True)
    outer.add_argument("--expected-controller-revision", required=True)
    outer.add_argument("--helper-image", required=True)
    outer.add_argument("--workspace", type=Path, required=True)
    outer.add_argument("--runtime-directory", type=Path, required=True)
    outer.add_argument("--signing-key", type=Path, required=True)
    outer.add_argument("--installation-id", required=True)
    outer.add_argument(
        "--docker-socket", type=Path, default=Path("/var/run/docker.sock")
    )
    args = parser.parse_args(argv)
    try:
        return run_inside(args) if args.mode == "inside" else run_outer(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"hosted campaign runtime: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
