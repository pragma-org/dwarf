#!/usr/bin/env python3
"""Qualify exact Cardano-node and Amaru releases on isolated real devnets.

This command is deliberately conservative.  It derives candidate topologies
from the retained upstream mixed baseline, creates a unique Compose project,
requires fresh project-owned volumes, captures runtime evidence, tears the
candidate down, and writes a *proposal* for catalog review.  It never changes
``versions/catalog.json`` or the retained known-good topology.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
REPOSITORY_ROOT = DWARF_ROOT.parent
for import_root in (DWARF_ROOT, SCRIPT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from check_cardano_amaru_topology import (  # noqa: E402
    AMARU_CONSUMER,
    AMARU_RELAYS,
    CARDANO_REFERENCE_NODES,
    collect_and_classify,
    query_cardano_tip,
)
from profile_manager.version_catalog import (  # noqa: E402
    DEFAULT_CATALOG_PATH,
    load_version_catalog,
    resolve_release,
)
from redeploy_cardano_amaru_topology import (  # noqa: E402
    fresh_readiness_proven,
    remember_bootstrap_evidence,
)


SCOPES = {"cardano-only", "amaru-only", "mixed"}
PROTECTED_PROJECTS = {
    "cardano_amaru_relay_bootstrap_control",
    "dwarf",
}
BASE_PACKAGE = REPOSITORY_ROOT / "antithesis" / "cardano_amaru_relay_bootstrap_control"
CONTROL_AMARU_IMAGE = (
    "ghcr.io/lambdasistemi/amaru-bootstrap-producer@"
    "sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36"
)
AMARU_TARGET_EXTRACT = "amaru-target-extract"
CARDANO_SERVICES = (*CARDANO_REFERENCE_NODES, AMARU_CONSUMER)
CARDANO_ONLY_SERVICES = {
    "configurator",
    "tracer",
    "tracer-sidecar",
    "log-tailer",
    *CARDANO_REFERENCE_NODES,
}
FATAL_SIGNAL_PATTERNS = (
    ("panic", re.compile(r"panicked at", re.IGNORECASE)),
    (
        "listener-address-in-use",
        re.compile(r"EADDRINUSE|Address already in use", re.IGNORECASE),
    ),
    ("consensus-terminated", re.compile(r"Consensus died", re.IGNORECASE)),
    (
        "invalid-future-rollback",
        re.compile(r"attempted roll back in the future", re.IGNORECASE),
    ),
    ("fatal-error", re.compile(r"fatal error", re.IGNORECASE)),
)
BACKGROUND_SIGNAL_PATTERNS = (
    ("known-vrf-key-bad-proof", re.compile(r"VRFKeyBadProof", re.IGNORECASE)),
)
TERMINAL_FAILURE_PATTERNS = (
    (
        "amaru-bootstrap-store-incompatible",
        re.compile(r"chain database cannot be migrated .* automatically", re.IGNORECASE | re.DOTALL),
    ),
    (
        "amaru-runtime-interface-incompatible",
        re.compile(r"unexpected argument .*migrate-chain-db|unknown (?:option|argument).*migrate", re.IGNORECASE),
    ),
    (
        "amaru-runtime-executable-incompatible",
        re.compile(r"amaru: cannot execute: required file not found", re.IGNORECASE),
    ),
)


class QualificationError(RuntimeError):
    """Qualification cannot safely or truthfully continue."""


def classify_log_signals(text: str) -> dict[str, list[str]]:
    """Separate terminal process failures from classified background signals."""
    return {
        "fatal": [name for name, pattern in FATAL_SIGNAL_PATTERNS if pattern.search(text)],
        "background": [
            name for name, pattern in BACKGROUND_SIGNAL_PATTERNS if pattern.search(text)
        ],
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    pieces = re.findall(r"\d+|[A-Za-z]+", str(version))
    return tuple((1, int(piece)) if piece.isdigit() else (0, piece.lower()) for piece in pieces)


def _stable_releases(catalog: dict[str, Any], implementation: str) -> list[dict[str, Any]]:
    releases = [
        release
        for release in catalog.get("releases", [])
        if release.get("implementation") == implementation
        and release.get("channel") == "stable"
        and any(
            artifact.get("kind") == "oci"
            and artifact.get("availability") == "available"
            and artifact.get("digest")
            for artifact in release.get("artifacts", [])
        )
    ]
    return sorted(
        releases,
        key=lambda release: (
            str(release.get("released_at") or ""),
            _version_key(str(release.get("version") or "")),
        ),
        reverse=True,
    )


def candidate_matrix(catalog: dict[str, Any], scope: str) -> list[dict[str, str]]:
    """Return exact, artifact-backed candidates newest-to-oldest."""
    if scope not in SCOPES:
        raise QualificationError(f"unknown qualification scope: {scope}")
    cardano = _stable_releases(catalog, "cardano-node")
    amaru = _stable_releases(catalog, "amaru")
    if scope == "cardano-only":
        return [{"cardano_version": item["version"]} for item in cardano]
    if scope == "amaru-only":
        return [{"amaru_version": item["version"]} for item in amaru]
    return [
        {"cardano_version": left["version"], "amaru_version": right["version"]}
        for left, right in itertools.product(cardano, amaru)
    ]


def exact_oci_reference(release: dict[str, Any]) -> str:
    artifacts = [
        artifact
        for artifact in release.get("artifacts", [])
        if artifact.get("kind") == "oci"
        and artifact.get("availability") == "available"
        and artifact.get("digest")
    ]
    if not artifacts:
        raise QualificationError(
            f"{release.get('implementation')} {release.get('version')} has no exact OCI artifact"
        )
    artifact = artifacts[0]
    reference = str(artifact["reference"])
    repository = reference.split("@", 1)[0]
    last_slash = repository.rfind("/")
    colon = repository.rfind(":")
    if colon > last_slash:
        repository = repository[:colon]
    return f"{repository}@{artifact['digest']}"


def build_project_name(
    scope: str,
    cardano_version: str | None,
    amaru_version: str | None,
    *,
    token: str | None = None,
) -> str:
    identity = "-".join(value for value in (cardano_version, amaru_version) if value)
    safe = re.sub(r"[^a-z0-9]+", "-", identity.lower()).strip("-")[:30]
    suffix = re.sub(r"[^a-z0-9]", "", (token or uuid.uuid4().hex[:8]).lower())[:12]
    project = f"dwarf-qual-{scope.replace('-only', '')}-{safe}-{suffix}".strip("-")
    if project in PROTECTED_PROJECTS:
        raise QualificationError(f"qualification project is protected: {project}")
    return project


def _uses_modern_amaru_runtime_interface(
    image: str, runtime_interface: str | None = None
) -> bool:
    if runtime_interface is not None:
        if runtime_interface not in {"extracted-binary", "legacy-wrapper"}:
            raise QualificationError(
                "amaru_runtime_interface must be extracted-binary or legacy-wrapper"
            )
        return runtime_interface == "extracted-binary"
    repository = str(image or "").split("@", 1)[0]
    last_slash = repository.rfind("/")
    if repository.rfind(":") > last_slash:
        repository = repository[: repository.rfind(":")]
    return repository == "ghcr.io/pragma-org/amaru"


def _append_volume(service: dict[str, Any], source: str, target: str) -> None:
    volumes = service.setdefault("volumes", [])
    if not isinstance(volumes, list):
        raise QualificationError("service volumes must be a list")
    if any(
        isinstance(item, dict)
        and item.get("source") == source
        and item.get("target") == target
        for item in volumes
    ):
        return
    volumes.append(
        {
            "type": "volume",
            "source": source,
            "target": target,
            "read_only": True,
        }
    )


def transform_compose_model(
    model: dict[str, Any],
    *,
    scope: str,
    project: str,
    cardano_image: str | None,
    amaru_image: str | None,
    allowed_project_prefix: str = "dwarf-qual-",
    amaru_runtime_interface: str | None = None,
    amaru_json_traces: bool = False,
) -> dict[str, Any]:
    """Namespace a rendered baseline and replace only candidate node images."""
    if scope not in SCOPES:
        raise QualificationError(f"unknown qualification scope: {scope}")
    if project in PROTECTED_PROJECTS:
        raise QualificationError(f"refusing to use protected project {project}")
    if not project.startswith(allowed_project_prefix):
        raise QualificationError(
            f"project must use the {allowed_project_prefix} prefix"
        )
    transformed = copy.deepcopy(model)
    transformed.pop("name", None)
    services = transformed.get("services")
    if not isinstance(services, dict):
        raise QualificationError("rendered Compose model has no services")
    if scope == "cardano-only":
        services = {
            name: service
            for name, service in services.items()
            if name in CARDANO_ONLY_SERVICES
        }
        transformed["services"] = services
    required = set(CARDANO_REFERENCE_NODES)
    if not required.issubset(services):
        raise QualificationError("baseline is missing Cardano producer/relay services")
    if scope in {"amaru-only", "mixed"} and not set(AMARU_RELAYS).issubset(services):
        raise QualificationError("baseline is missing Amaru relay services")

    modern_amaru = bool(
        amaru_image
        and _uses_modern_amaru_runtime_interface(
            amaru_image, runtime_interface=amaru_runtime_interface
        )
    )
    for name, service in list(services.items()):
        if not isinstance(service, dict):
            continue
        service.pop("container_name", None)
        labels = service.setdefault("labels", {})
        if isinstance(labels, dict):
            labels["com.dwarf.qualification"] = "true"
            labels["com.dwarf.qualification.scope"] = scope
        if name in CARDANO_SERVICES and cardano_image:
            service["image"] = cardano_image
        if name in AMARU_RELAYS and amaru_image:
            environment = service.setdefault("environment", {})
            if not isinstance(environment, dict):
                raise QualificationError(f"{name} has a non-mapping environment")
            environment["AMARU_PEER"] = (
                "p1.example:3001" if name == "amaru-relay-1" else "p2.example:3001"
            )
            if amaru_json_traces:
                environment["AMARU_WITH_JSON_TRACES"] = "true"
            if modern_amaru:
                # Preserve the proven self-bootstrap wrapper and substitute
                # only the exact target Amaru executable.  The official image
                # is retained as an exited extraction service, so its immutable
                # artifact identity remains mechanically observable.
                service["image"] = CONTROL_AMARU_IMAGE
                environment["AMARU_BIN"] = "/target/amaru"
                environment["AMARU_MIGRATE_CHAIN_DB"] = "true"
                _append_volume(service, "amaru-target-bin", "/target")
                dependencies = service.setdefault("depends_on", {})
                if not isinstance(dependencies, dict):
                    raise QualificationError(f"{name} has invalid depends_on")
                dependencies[AMARU_TARGET_EXTRACT] = {
                    "condition": "service_completed_successfully",
                    "required": True,
                }
            else:
                service["image"] = amaru_image
                environment.pop("AMARU_BIN", None)
                environment.pop("AMARU_MIGRATE_CHAIN_DB", None)

    if modern_amaru and amaru_image:
        services[AMARU_TARGET_EXTRACT] = {
            "image": amaru_image,
            "user": "0:0",
            "entrypoint": ["/bin/sh", "-ec"],
            "command": [
                "cp /usr/local/bin/amaru /target/amaru && chmod 0755 /target/amaru"
            ],
            "volumes": [
                {"type": "volume", "source": "amaru-target-bin", "target": "/target"}
            ],
            "labels": {
                "com.dwarf.qualification": "true",
                "com.dwarf.qualification.scope": scope,
                "com.antithesis.exclude_from_faults": "network,kill,pause,stop",
            },
        }
        transformed.setdefault("volumes", {})["amaru-target-bin"] = {}

    if scope in {"amaru-only", "mixed"}:
        consumer = services.get("amaru-consumer")
        seed = services.get("amaru-consumer-seed")
        if not isinstance(seed, dict) or not isinstance(consumer, dict):
            raise QualificationError(
                "baseline is missing the live-bootstrap seed or isolated consumer"
            )

    for volume in (transformed.get("volumes") or {}).values():
        if isinstance(volume, dict):
            volume.pop("external", None)
            volume.pop("name", None)
    networks = transformed.get("networks") or {}
    for name, network in networks.items():
        if isinstance(network, dict):
            network["name"] = f"{project}-{name}"
    transformed["x-dwarf-qualification"] = {
        "scope": scope,
        "contract": "cardano-devnet" if scope == "cardano-only" else "amaru-relay-consumer",
        "project": project,
        "fresh_state_required": True,
    }
    return transformed


def classify_qualification(scope: str, gates: dict[str, bool]) -> dict[str, Any]:
    required = {
        "fresh_state",
        "exact_identity",
        "required_services",
        "chain_progress",
        "peer_formation",
        "no_fatal_signatures",
        "no_restart_loop",
        "clean_teardown",
    }
    if scope in {"amaru-only", "mixed"}:
        required |= {"consumer_amaru_only_path", "consumer_converged"}
    failed = sorted(name for name in required if gates.get(name) is not True)
    return {
        "passed": not failed,
        "classification": "passed-all-gates" if not failed else "runtime-contract-failed",
        "required_gates": sorted(required),
        "failed_gates": failed,
        "gates": {name: gates.get(name) is True for name in sorted(required)},
    }


def classify_terminal_runtime_failure(logs: str) -> str | None:
    for code, pattern in TERMINAL_FAILURE_PATTERNS:
        if pattern.search(logs or ""):
            return code
    return None


def propose_catalog_update(result: dict[str, Any]) -> dict[str, Any]:
    terminal_failure = str(result.get("terminal_failure") or "")
    if result.get("passed"):
        proposed_status = "confirmed"
    elif terminal_failure in {
        "amaru-bootstrap-store-incompatible",
        "amaru-runtime-interface-incompatible",
    }:
        proposed_status = "incompatible"
    else:
        proposed_status = "unknown"
    return {
        "schema_version": 1,
        "apply_automatically": False,
        "review_required": True,
        "scope": result.get("scope"),
        "candidate": result.get("candidate"),
        "proposed_status": proposed_status,
        "reason": terminal_failure or result.get("classification"),
        "checked_at": result.get("completed_at"),
        "classification": result.get("classification"),
        "evidence": [f"qualification:{result.get('evidence_root')}"] if result.get("evidence_root") else [],
    }


def _run(
    args: list[str],
    *,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env=env,
    )


def _require(result: subprocess.CompletedProcess[str], description: str) -> str:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise QualificationError(f"{description} failed: {detail}")
    return result.stdout


def _compose(project: str, compose_file: Path, *arguments: str) -> list[str]:
    return ["docker", "compose", "-p", project, "-f", str(compose_file), *arguments]


def _project_containers(project: str) -> dict[str, str]:
    result = _run([
        "docker", "ps", "-a",
        "--filter", f"label=com.docker.compose.project={project}",
        "--format", '{{.Label "com.docker.compose.service"}}\t{{.Names}}',
    ])
    _require(result, "container inventory")
    found: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        service, separator, name = line.partition("\t")
        if separator and service and name:
            found.setdefault(service, []).append(name)
    ambiguous = [service for service, names in found.items() if len(names) != 1]
    if ambiguous:
        raise QualificationError(f"ambiguous containers for services: {', '.join(ambiguous)}")
    return {service: names[0] for service, names in found.items()}


def _project_is_fresh(project: str) -> bool:
    if _project_containers(project):
        return False
    for kind in ("volume", "network"):
        result = _run([
            "docker", kind, "ls", "-q",
            "--filter", f"label=com.docker.compose.project={project}",
        ])
        if result.returncode != 0 or result.stdout.strip():
            return False
    return True


def _amaru_log_text(project: str) -> str:
    containers = _project_containers(project)
    chunks: list[str] = []
    tracer = containers.get("tracer-sidecar")
    if tracer:
        result = _run(
            [
                "docker", "exec", tracer, "/bin/sh", "-c",
                "for file in /opt/amaru-logs/*.log; do "
                "test -f \"$file\" || continue; echo \"[amaru-log:$file]\"; tail -n 400 \"$file\"; done",
            ],
            timeout=60,
        )
        if result.stdout:
            chunks.append(result.stdout)
        if result.stderr:
            chunks.append(f"[tracer-sidecar-stderr]\n{result.stderr}")
    # The upstream tracer-sidecar image may be distroless and therefore have
    # no shell.  Candidate process stderr is authoritative for startup/migration
    # failures, so retain direct Docker logs as a non-vacuous fallback and as a
    # cross-check even when shared trace files are readable.
    for service, container in sorted(containers.items()):
        if not service.startswith("amaru-relay-"):
            continue
        result = _run(["docker", "logs", "--tail", "400", container], timeout=60)
        body = result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else "")
        if body:
            chunks.append(f"[amaru-service:{service}]\n{body}")
    return "\n".join(chunks)


def identity_matches(
    *,
    expected_version: str,
    reported_version: str,
    expected_image_id: str,
    running_image_id: str,
) -> bool:
    """Require process-level version proof and container artifact identity."""
    return bool(
        expected_version
        and expected_version in reported_version
        and expected_image_id
        and running_image_id == expected_image_id
    )


def _image_id(reference: str) -> str:
    result = _run(["docker", "image", "inspect", reference, "--format", "{{.Id}}"])
    return _require(result, f"image inspection for {reference}").strip()


def _identity_observation(
    project: str,
    *,
    scope: str,
    cardano_version: str,
    amaru_version: str | None,
    cardano_image: str,
    amaru_image: str | None,
    amaru_runtime_interface: str | None = None,
    measurement_target_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    containers = _project_containers(project)
    modern_amaru = bool(
        amaru_image
        and _uses_modern_amaru_runtime_interface(
            amaru_image, runtime_interface=amaru_runtime_interface
        )
    )
    extractor_container = containers.get(AMARU_TARGET_EXTRACT) if modern_amaru else None
    extractor_image_id = ""
    if extractor_container:
        extractor_inspect = _run(
            ["docker", "inspect", extractor_container, "--format", "{{.Image}}"]
        )
        if extractor_inspect.returncode == 0:
            extractor_image_id = extractor_inspect.stdout.strip()
    wrapper_image_id = _image_id(CONTROL_AMARU_IMAGE) if modern_amaru else ""
    patched_target = bool(
        measurement_target_identity
        and measurement_target_identity.get("target_mode") == "patched"
    )
    patched_target_record: dict[str, Any] | None = None
    if patched_target:
        label_result = _run(
            [
                "docker",
                "image",
                "inspect",
                str(amaru_image),
                "--format",
                "{{json .Config.Labels}}",
            ],
            timeout=30,
        )
        try:
            image_labels = json.loads(label_result.stdout) if label_result.returncode == 0 else {}
        except (TypeError, json.JSONDecodeError):
            image_labels = {}
        expected_source = str(measurement_target_identity.get("source_revision") or "")
        expected_patch = str(measurement_target_identity.get("patch_set_sha256") or "")
        labels_matched = bool(
            image_labels.get("org.opencontainers.image.revision") == expected_source
            and image_labels.get("org.dwarf.measurement.patch-sha256") == expected_patch
        )
        patched_target_record = {
            "source_revision": expected_source,
            "patch_set_sha256": expected_patch,
            "executable_digest": measurement_target_identity.get("executable_digest"),
            "image": measurement_target_identity.get("image"),
            "image_digest": measurement_target_identity.get("image_digest"),
            "image_labels": image_labels,
            "labels_matched": labels_matched,
            "identity_record_matched": bool(
                measurement_target_identity.get("image") == amaru_image
                and measurement_target_identity.get("image_digest") == _image_id(str(amaru_image))
            ),
        }
    services = list(CARDANO_REFERENCE_NODES)
    if scope in {"amaru-only", "mixed"}:
        services.extend([AMARU_CONSUMER, *AMARU_RELAYS])
    records: dict[str, Any] = {}
    for service in services:
        container = containers.get(service)
        expected = amaru_version if service in AMARU_RELAYS else cardano_version
        expected_image = amaru_image if service in AMARU_RELAYS else cardano_image
        expected_image_id = _image_id(expected_image) if expected_image else ""
        running_image_result = (
            _run(["docker", "inspect", container, "--format", "{{.Image}}"])
            if container
            else None
        )
        running_image_id = (
            running_image_result.stdout.strip()
            if running_image_result is not None and running_image_result.returncode == 0
            else ""
        )
        artifact_container = (
            extractor_container if service in AMARU_RELAYS and modern_amaru else container
        )
        artifact_image_id = (
            extractor_image_id
            if service in AMARU_RELAYS and modern_amaru
            else running_image_id
        )
        commands = (
            [["docker", "exec", container, "/target/amaru", "--version"],
             ["docker", "exec", container, "/usr/local/bin/amaru", "--version"],
             ["docker", "exec", container, "/bin/amaru", "--version"]]
            if service in AMARU_RELAYS
            else [["docker", "exec", container, "cardano-node", "--version"]]
        ) if container else []
        result = None
        version_command: list[str] = []
        for command in commands:
            attempt = _run(command, timeout=30)
            if attempt.returncode == 0:
                result = attempt
                version_command = command
                break
        output = ((result.stdout + result.stderr) if result else "").strip()
        reported_source_matched = None
        executable_digest_matched = None
        executable_digest_reported = None
        if service in AMARU_RELAYS and patched_target:
            expected_source = str(measurement_target_identity.get("source_revision") or "")
            reported_source_matched = bool(
                len(expected_source) == 40 and expected_source[:8] in output
            )
            executable_result = _run(
                ["docker", "exec", container, "sha256sum", "/target/amaru"],
                timeout=30,
            )
            if executable_result.returncode == 0:
                executable_digest_reported = "sha256:" + executable_result.stdout.split()[0]
            executable_digest_matched = bool(
                executable_digest_reported
                == measurement_target_identity.get("executable_digest")
            )
            artifact_match = bool(
                expected_image_id
                and artifact_image_id == expected_image_id
                and patched_target_record
                and patched_target_record["labels_matched"]
                and patched_target_record["identity_record_matched"]
                and reported_source_matched
                and executable_digest_matched
            )
        else:
            artifact_match = identity_matches(
                expected_version=str(expected or ""),
                reported_version=output,
                expected_image_id=expected_image_id,
                running_image_id=artifact_image_id,
            )
        wrapper_match = not (
            service in AMARU_RELAYS and modern_amaru
        ) or running_image_id == wrapper_image_id
        records[service] = {
            "container": container,
            "artifact_container": artifact_container,
            "expected_version": expected,
            "reported": output,
            "version_command": version_command,
            "expected_image": expected_image,
            "expected_image_id": expected_image_id,
            "running_image_id": running_image_id,
            "artifact_image_id": artifact_image_id,
            "wrapper_expected_image": (
                CONTROL_AMARU_IMAGE
                if service in AMARU_RELAYS and modern_amaru
                else None
            ),
            "wrapper_expected_image_id": (
                wrapper_image_id
                if service in AMARU_RELAYS and modern_amaru
                else None
            ),
            "wrapper_matched": wrapper_match,
            "reported_source_matched": reported_source_matched,
            "executable_digest_reported": executable_digest_reported,
            "executable_digest_matched": executable_digest_matched,
            "matched": bool(
                container
                and result
                and artifact_container
                and artifact_match
                and wrapper_match
            ),
        }
    result = {
        "matched": bool(records) and all(item["matched"] for item in records.values()),
        "services": records,
    }
    if patched_target_record is not None:
        result["patched_target"] = patched_target_record
    return result


def _observe_cardano(project: str, sample_seconds: float) -> dict[str, Any]:
    containers = _project_containers(project)
    required = list(CARDANO_REFERENCE_NODES)
    inspect_records: dict[str, Any] = {}
    for service in required:
        name = containers.get(service)
        body: dict[str, Any] = {}
        if name:
            inspected = _run(["docker", "inspect", name])
            if inspected.returncode == 0:
                body = json.loads(inspected.stdout)[0]
        state = body.get("State") or {}
        inspect_records[service] = {
            "present": bool(body),
            "running": bool(state.get("Running")),
            "restart_count": int(body.get("RestartCount") or 0),
            "oom_killed": bool(state.get("OOMKilled")),
            "image": body.get("Image"),
        }

    def tips() -> dict[str, Any]:
        return {
            service: query_cardano_tip(containers[service])
            for service in required
            if service in containers
        }

    first = tips()
    time.sleep(max(0.0, sample_seconds))
    final = tips()
    slots = [
        int((final.get(service) or {}).get("slot"))
        for service in required
        if isinstance((final.get(service) or {}).get("slot"), int)
    ]
    required_ok = all(
        record["present"] and record["running"] and not record["oom_killed"]
        for record in inspect_records.values()
    )
    progress = all(
        isinstance((first.get(service) or {}).get("slot"), int)
        and isinstance((final.get(service) or {}).get("slot"), int)
        and int(final[service]["slot"]) > int(first[service]["slot"])
        for service in required
    )
    converged = len(slots) == len(required) and max(slots) - min(slots) <= 6
    return {
        "containers": inspect_records,
        "samples": [{"tips": first}, {"tips": final}],
        "required_services": required_ok,
        "chain_progress": progress,
        "peer_formation": converged,
        "no_restart_loop": all(record["restart_count"] == 0 for record in inspect_records.values()),
    }


def _render_baseline(package: Path) -> dict[str, Any]:
    compose_file = package / "docker-compose.yaml"
    if not compose_file.is_file():
        raise QualificationError(f"baseline Compose file is missing: {compose_file}")
    env = dict(os.environ)
    env["INTERNAL_NETWORK"] = "false"
    output = _require(
        _run(["docker", "compose", "-f", str(compose_file), "config", "--format", "json"], timeout=120, env=env),
        "baseline Compose render",
    )
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise QualificationError("baseline Compose render was not JSON") from error


def _candidate_versions(
    catalog: dict[str, Any],
    scope: str,
    cardano_version: str | None,
    amaru_version: str | None,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any] | None]:
    candidates = candidate_matrix(catalog, scope)
    if not candidates:
        raise QualificationError(f"no artifact-backed stable candidates for {scope}")
    selected = dict(candidates[0])
    if cardano_version:
        selected["cardano_version"] = cardano_version
    if amaru_version:
        selected["amaru_version"] = amaru_version
    if scope == "amaru-only":
        selected["cardano_version"] = cardano_version or "10.7.1"
    cardano_release = resolve_release(catalog, "cardano-node", selected["cardano_version"])
    amaru_release = (
        resolve_release(catalog, "amaru", selected["amaru_version"])
        if selected.get("amaru_version")
        else None
    )
    return selected, cardano_release, amaru_release


def run_qualification(
    *,
    scope: str,
    catalog_path: Path,
    base_package: Path,
    state_dir: Path,
    cardano_version: str | None = None,
    amaru_version: str | None = None,
    timeout_seconds: int = 1800,
    sample_seconds: float = 10.0,
    dry_run: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    catalog = load_version_catalog(catalog_path)
    candidate, cardano_release, amaru_release = _candidate_versions(
        catalog, scope, cardano_version, amaru_version
    )
    project = build_project_name(
        scope, candidate.get("cardano_version"), candidate.get("amaru_version")
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_root = state_dir.resolve() / "version-qualifications" / f"{stamp}-{project}"
    evidence_root.mkdir(parents=True, exist_ok=False)

    def emit(stage: str, **details: Any) -> None:
        event = {"at": _utc_now(), "stage": stage, **details}
        events.append(event)
        if progress:
            progress(event)

    events: list[dict[str, Any]] = []
    cardano_image = exact_oci_reference(cardano_release)
    amaru_image = exact_oci_reference(amaru_release) if amaru_release else None
    model = transform_compose_model(
        _render_baseline(base_package),
        scope=scope,
        project=project,
        cardano_image=cardano_image,
        amaru_image=amaru_image,
    )
    compose_file = evidence_root / "qualification-compose.json"
    compose_file.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fresh = _project_is_fresh(project)
    if not fresh:
        raise QualificationError(f"qualification project is not fresh: {project}")
    emit("prepared", project=project, candidate=candidate, evidence_root=str(evidence_root))
    if dry_run:
        result = {
            "schema_version": 1,
            "scope": scope,
            "candidate": candidate,
            "project": project,
            "dry_run": True,
            "passed": False,
            "classification": "dry-run-only",
            "evidence_root": str(evidence_root),
            "events": events,
        }
        (evidence_root / "qualification-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return result

    gates = {"fresh_state": fresh}
    observation: dict[str, Any] = {}
    identity: dict[str, Any] = {}
    logs = ""
    log_signals: dict[str, list[str]] = {"fatal": [], "background": []}
    started = False
    teardown_ok = False
    error: str | None = None
    terminal_failure: str | None = None
    try:
        _require(
            _run(
                _compose(project, compose_file, "up", "-d"),
                timeout=max(900, timeout_seconds),
            ),
            "qualification startup",
        )
        started = True
        emit("started")
        deadline = time.monotonic() + max(1, timeout_seconds)
        bootstrap_evidence: dict[str, Any] = {}
        attempt = 0
        while time.monotonic() <= deadline:
            attempt += 1
            if scope == "cardano-only":
                observation = _observe_cardano(project, sample_seconds)
                ready = bool(
                    observation.get("required_services")
                    and observation.get("chain_progress")
                    and observation.get("peer_formation")
                )
            else:
                observation = collect_and_classify(
                    project=project,
                    output=evidence_root / f"health-{attempt:03d}.json",
                    sample_seconds=sample_seconds,
                )
                bootstrap_evidence = remember_bootstrap_evidence(observation, bootstrap_evidence)
                ready = fresh_readiness_proven(observation, bootstrap_evidence)
            emit("readiness", attempt=attempt, ready=ready, state=observation.get("state"))
            if ready:
                break
            if scope in {"amaru-only", "mixed"} and observation.get("state") == "unhealthy":
                terminal_failure = classify_terminal_runtime_failure(_amaru_log_text(project))
                if terminal_failure:
                    error = terminal_failure
                    emit("terminal_failure", code=terminal_failure)
                    break
            time.sleep(max(1.0, sample_seconds))

        identity = _identity_observation(
            project,
            scope=scope,
            cardano_version=candidate["cardano_version"],
            amaru_version=candidate.get("amaru_version"),
            cardano_image=cardano_image,
            amaru_image=amaru_image,
        )
        (evidence_root / "identity.json").write_text(
            json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        logs_result = _run(_compose(project, compose_file, "logs", "--no-color", "--timestamps"), timeout=300)
        logs = logs_result.stdout + ("\n[stderr]\n" + logs_result.stderr if logs_result.stderr else "")
        logs += "\n" + _amaru_log_text(project)
        (evidence_root / "compose-logs.txt").write_text(logs, encoding="utf-8")
        ps = _run(_compose(project, compose_file, "ps", "--all"), timeout=60)
        (evidence_root / "compose-ps.txt").write_text(ps.stdout + ps.stderr, encoding="utf-8")

        if scope == "cardano-only":
            gates.update({
                "required_services": bool(observation.get("required_services")),
                "chain_progress": bool(observation.get("chain_progress")),
                "peer_formation": bool(observation.get("peer_formation")),
                "no_restart_loop": bool(observation.get("no_restart_loop")),
            })
        else:
            healthy = fresh_readiness_proven(observation, bootstrap_evidence)
            containers = ((observation.get("observation") or {}).get("containers") or {})
            gates.update({
                "required_services": observation.get("state") == "healthy",
                "chain_progress": healthy,
                "peer_formation": observation.get("state") == "healthy",
                "consumer_amaru_only_path": ((observation.get("observation") or {}).get("peer_contract") or {}).get("amaru_consumer_only_amaru_upstreams") is True,
                "consumer_converged": healthy,
                "no_restart_loop": all(int(item.get("restart_count") or 0) == 0 for item in containers.values()),
            })
        gates["exact_identity"] = bool(identity.get("matched"))
        log_signals = classify_log_signals(logs)
        gates["no_fatal_signatures"] = not log_signals["fatal"]
    except Exception as exc:  # evidence and teardown are mandatory on every failure path
        error = str(exc)
        emit("failed", error=error)
    finally:
        if started:
            down = _run(_compose(project, compose_file, "down", "-v", "--remove-orphans"), timeout=600)
            teardown_ok = down.returncode == 0 and _project_is_fresh(project)
            (evidence_root / "teardown.txt").write_text(
                down.stdout + ("\n[stderr]\n" + down.stderr if down.stderr else ""), encoding="utf-8"
            )
        else:
            teardown_ok = _project_is_fresh(project)
        gates["clean_teardown"] = teardown_ok
        emit("teardown", passed=teardown_ok)

    classified = classify_qualification(scope, gates)
    completed_at = _utc_now()
    result = {
        "schema_version": 1,
        "scope": scope,
        "candidate": candidate,
        "project": project,
        "started_at": events[0]["at"],
        "completed_at": completed_at,
        "evidence_root": str(evidence_root),
        "error": error,
        "terminal_failure": terminal_failure,
        "observation": observation,
        "identity": identity,
        "log_signals": log_signals,
        "events": events,
        **classified,
    }
    (evidence_root / "qualification-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (evidence_root / "catalog-update-proposal.json").write_text(
        json.dumps(propose_catalog_update(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, choices=sorted(SCOPES))
    parser.add_argument("--cardano-version")
    parser.add_argument("--amaru-version")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--base-package", type=Path, default=BASE_PACKAGE)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("DWARF_STATE_DIR") or Path.home() / ".local/share/dwarf/state"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--sample-seconds", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    def show(event: dict[str, Any]) -> None:
        print(json.dumps(event, sort_keys=True), flush=True)

    try:
        result = run_qualification(
            scope=args.scope,
            catalog_path=args.catalog,
            base_package=args.base_package,
            state_dir=args.state_dir,
            cardano_version=args.cardano_version,
            amaru_version=args.amaru_version,
            timeout_seconds=max(1, args.timeout_seconds),
            sample_seconds=max(0.0, args.sample_seconds),
            dry_run=args.dry_run,
            progress=show,
        )
    except Exception as error:
        print(json.dumps({"passed": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    if result.get("dry_run"):
        return 0
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
