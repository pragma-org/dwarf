"""Bind explicit scenario measurements to a proven deployed real-node runtime."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from profile_manager.measurement_collectors.amaru_factory import (
    build_amaru_measurement_factories,
)
from profile_manager.measurement_collectors.amaru_patched import (
    AMARU_MEASUREMENT_PATCH_SHA256,
    AMARU_SOURCE_REVISION,
)
from profile_manager.measurement_collectors.cardano_factory import (
    build_cardano_measurement_factories,
)
from profile_manager.measurement_collectors.cardano_patched import (
    CARDANO_MEASUREMENT_PATCH_SHA256,
    CARDANO_SOURCE_REVISION,
)
from profile_manager.measurement_resolution import resolve_measurements
from scripts.runtime_amaru_preview_proof import extract_latest_adopted_tip


AMARU_STOCK_CAPABILITIES = {
    "amaru-header-lifecycle-metrics",
    "amaru-json-traces",
    "amaru-fork-switch-metrics",
    "amaru-mempool-metrics",
    "amaru-ledger-rule-traces",
    "amaru-transaction-validation-spans",
    "amaru-plutus-execution-spans",
    "amaru-block-epoch-spans",
    "amaru-protocol-metrics",
    "amaru-network-traces",
    "amaru-system-metrics",
    "dwarf-container-metrics",
    "dwarf-target-lifecycle",
    "amaru-readiness-probe",
    "dwarf-chain-tip-probe",
    "amaru-chain-tip",
    "dwarf-workload-events",
}
AMARU_PATCHED_CAPABILITIES = AMARU_STOCK_CAPABILITIES | {
    "amaru-measurement-patch-protocol-decode",
    "amaru-measurement-patch-blockfetch",
    "amaru-measurement-patch-txsubmission2",
    "amaru-patch-identity",
}

CARDANO_STOCK_CAPABILITIES = {
    "cardano-new-tracing",
    "cardano-chainsync-traces",
    "cardano-chaindb-traces",
    "cardano-blockfetch-traces",
    "cardano-txsubmission-traces",
    "cardano-mempool-traces",
    "cardano-ledger-metrics",
    "cardano-network-traces",
    "cardano-keepalive-traces",
    "cardano-resource-traces",
    "dwarf-container-metrics",
    "dwarf-target-lifecycle",
    "cardano-readiness-probe",
    "dwarf-chain-tip-probe",
    "dwarf-workload-events",
}
CARDANO_PATCHED_CAPABILITIES = CARDANO_STOCK_CAPABILITIES | {
    "cardano-patched-protocol-decode",
    "cardano-patched-ledger-plutus-stages",
}


class MeasurementExecutionError(RuntimeError):
    """The selected measurements cannot be bound to proven runtime evidence."""


def _selected_release(runtime: dict[str, Any], implementation: str) -> dict[str, Any]:
    provenance = runtime.get("versions") or runtime.get("version_provenance") or {}
    snapshot = (provenance.get("catalog_snapshot") or {})
    for release in snapshot.get("selected_releases") or []:
        if release.get("implementation") == implementation:
            return release
    raise MeasurementExecutionError(
        f"runtime catalog snapshot does not contain {implementation}"
    )


def _catalog_revision(runtime: dict[str, Any]) -> str:
    provenance = runtime.get("versions") or runtime.get("version_provenance") or {}
    return str(
        provenance.get("catalog_revision")
        or (provenance.get("catalog_snapshot") or {}).get("catalog_revision")
        or ""
    )


def _stock_amaru_identity(runtime: dict[str, Any], scenario) -> dict[str, Any]:
    if runtime.get("profile_id") != scenario.profile:
        raise MeasurementExecutionError("runtime profile does not match scenario profile")
    runtime_identity = runtime.get("identity") or {}
    if runtime_identity.get("matched") is not True:
        raise MeasurementExecutionError("deployed Amaru identity is not proven")
    release = _selected_release(runtime, "amaru")
    version = str(release.get("version") or "")
    source_revision = str(release.get("source_revision") or "")
    if version != scenario.target.get("version"):
        raise MeasurementExecutionError(
            f"deployed Amaru version {version or 'unknown'} does not match scenario"
        )
    artifact = next(
        (
            item
            for item in release.get("artifacts") or []
            if item.get("kind") == "oci" and item.get("availability") == "available"
        ),
        None,
    )
    if artifact is None:
        raise MeasurementExecutionError("runtime catalog has no immutable Amaru image")
    digest = str(artifact.get("digest") or "")
    image_reference = str((runtime.get("images") or {}).get("amaru") or "")
    service = ((runtime_identity.get("services") or {}).get("amaru-relay-1") or {})
    if service.get("matched") is not True:
        raise MeasurementExecutionError("Amaru relay identity is not proven")
    if service.get("artifact_image_id") != digest:
        raise MeasurementExecutionError("deployed Amaru image digest does not match catalog")
    if not image_reference.endswith("@" + digest):
        raise MeasurementExecutionError("runtime Amaru image reference is not digest-pinned")
    return {
        "implementation": "amaru",
        "version": version,
        "source_revision": source_revision,
        "mode": "stock",
        "image_reference": image_reference,
        "image_digest": digest,
        "executable_digest": None,
        "version_catalog_revision": _catalog_revision(runtime),
    }


def _immutable_sha256(value: Any, field: str) -> str:
    text = str(value or "")
    if not text.startswith("sha256:") or len(text) != 71:
        raise MeasurementExecutionError(
            f"deployed patched Amaru {field} is not immutable"
        )
    return text


def _patched_amaru_identity(runtime: dict[str, Any], scenario) -> dict[str, Any]:
    if runtime.get("profile_id") != scenario.profile:
        raise MeasurementExecutionError("runtime profile does not match scenario profile")
    runtime_identity = runtime.get("identity") or {}
    if runtime_identity.get("matched") is not True:
        raise MeasurementExecutionError("deployed Amaru identity is not proven")
    release = _selected_release(runtime, "amaru")
    target = runtime.get("measurement_target") or {}
    if target.get("target_mode") != "patched":
        raise MeasurementExecutionError("runtime is not a patched Amaru target")
    version = str(target.get("version") or "")
    if version != scenario.target.get("version") or version != str(release.get("version") or ""):
        raise MeasurementExecutionError(
            f"deployed patched Amaru version {version or 'unknown'} does not match scenario"
        )
    source_revision = str(target.get("source_revision") or "")
    if (
        source_revision != AMARU_SOURCE_REVISION
        or source_revision != str(release.get("source_revision") or "")
    ):
        raise MeasurementExecutionError(
            "deployed patched Amaru source revision does not match collector"
        )
    patch_set = str(target.get("patch_set_sha256") or "")
    if patch_set != AMARU_MEASUREMENT_PATCH_SHA256:
        raise MeasurementExecutionError(
            "deployed patched Amaru patch-set identity does not match collector"
        )
    image_digest = _immutable_sha256(target.get("image_digest"), "image digest")
    executable_digest = _immutable_sha256(
        target.get("executable_digest"), "executable digest"
    )
    build_result_sha256 = _immutable_sha256(
        target.get("build_result_sha256"), "build result"
    )
    runtime_probe_log_sha256 = _immutable_sha256(
        target.get("runtime_probe_log_sha256"), "runtime probe log"
    )
    image_reference = str(target.get("image") or target.get("image_reference") or "")
    if not image_reference.endswith("@" + image_digest):
        raise MeasurementExecutionError(
            "runtime patched Amaru image reference is not digest-pinned"
        )
    service = ((runtime_identity.get("services") or {}).get("amaru-relay-1") or {})
    if service.get("matched") is not True:
        raise MeasurementExecutionError("patched Amaru relay identity is not proven")
    if str(service.get("artifact_image_id") or "") != image_digest:
        raise MeasurementExecutionError(
            "deployed patched Amaru image digest does not match runtime"
        )
    if service.get("executable_digest_matched") is not True:
        raise MeasurementExecutionError(
            "deployed patched Amaru executable identity is not proven"
        )
    if str(service.get("executable_digest_reported") or "") != executable_digest:
        raise MeasurementExecutionError(
            "deployed patched Amaru executable digest does not match runtime"
        )
    return {
        "implementation": "amaru",
        "version": version,
        "source_revision": source_revision,
        "mode": "patched",
        "patch_set_sha256": patch_set,
        "image_reference": image_reference,
        "image_digest": image_digest,
        "executable_digest": executable_digest,
        "build_result_sha256": build_result_sha256,
        "runtime_probe_log_sha256": runtime_probe_log_sha256,
        "version_catalog_revision": _catalog_revision(runtime),
    }


def _stock_cardano_identity(runtime: dict[str, Any], scenario) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime_profile = str(runtime.get("profile_id") or "")
    if runtime_profile != scenario.profile:
        raise MeasurementExecutionError("runtime profile does not match scenario profile")
    release = _selected_release(runtime, "cardano-node")
    version = str(release.get("version") or "")
    source_revision = str(release.get("source_revision") or "")
    if version != scenario.target.get("version"):
        raise MeasurementExecutionError(
            f"deployed Cardano-node version {version or 'unknown'} does not match scenario"
        )
    artifact = next(
        (
            item
            for item in release.get("artifacts") or []
            if item.get("kind") == "oci" and item.get("availability") == "available"
        ),
        None,
    )
    if artifact is None:
        raise MeasurementExecutionError("runtime catalog has no immutable Cardano-node image")
    digest = str(artifact.get("digest") or "")
    nodes = [
        node
        for node in (runtime.get("nodes") or runtime.get("haskell_nodes") or [])
        if node.get("impl") == "cardano-node"
    ]
    if not nodes:
        raise MeasurementExecutionError("runtime has no Cardano-node target")
    node = nodes[0]
    artifact_identity = node.get("artifact_identity") or {}
    version_identity = node.get("version_identity") or {}
    if artifact_identity.get("satisfied") is not True or version_identity.get("satisfied") is not True:
        raise MeasurementExecutionError("deployed Cardano-node identity is not proven")
    observed_digest = str(
        artifact_identity.get("image_digest")
        or artifact_identity.get("container_image_id")
        or node.get("image_digest")
        or ""
    )
    if str(node.get("source_revision") or "") != source_revision:
        raise MeasurementExecutionError("deployed Cardano-node source revision does not match catalog")
    image_reference = str(node.get("image_ref") or node.get("image") or "")
    mode = str(node.get("target_mode") or "stock")
    if mode == "patched":
        if source_revision != CARDANO_SOURCE_REVISION:
            raise MeasurementExecutionError("patched Cardano-node source revision does not match collector")
        if node.get("patch_set_sha256") != CARDANO_MEASUREMENT_PATCH_SHA256:
            raise MeasurementExecutionError("patched Cardano-node patch-set identity does not match collector")
        digest = str(node.get("image_digest") or observed_digest)
        if observed_digest != digest:
            raise MeasurementExecutionError("deployed patched Cardano-node image digest does not match runtime")
        for field in (
            "image_digest", "executable_digest", "build_result_sha256",
            "runtime_probe_log_sha256",
        ):
            value = str(node.get(field) or "")
            if not value.startswith("sha256:") or len(value) != 71:
                raise MeasurementExecutionError(
                    f"deployed patched Cardano-node {field} is not immutable"
                )
        if not image_reference.endswith("@" + digest):
            raise MeasurementExecutionError(
                "runtime patched Cardano-node image reference is not digest-pinned"
            )
        return ({
            "implementation": "cardano-node",
            "version": version,
            "source_revision": source_revision,
            "mode": "patched",
            "patch_set_sha256": node["patch_set_sha256"],
            "image_reference": image_reference,
            "image_digest": digest,
            "executable_digest": node["executable_digest"],
            "build_result_sha256": node["build_result_sha256"],
            "runtime_probe_log_sha256": node["runtime_probe_log_sha256"],
            "version_catalog_revision": _catalog_revision(runtime),
        }, node)
    if mode != "stock":
        raise MeasurementExecutionError(f"unsupported Cardano-node target mode: {mode}")
    if observed_digest != digest:
        raise MeasurementExecutionError("deployed Cardano-node image digest does not match catalog")
    if not image_reference.endswith("@" + digest):
        raise MeasurementExecutionError("runtime Cardano-node image reference is not digest-pinned")
    return ({
        "implementation": "cardano-node",
        "version": version,
        "source_revision": source_revision,
        "mode": "stock",
        "image_reference": image_reference,
        "image_digest": digest,
        "executable_digest": None,
        "version_catalog_revision": _catalog_revision(runtime),
    }, node)


def _docker_tip_probe(runtime: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    service = (((runtime.get("identity") or {}).get("services") or {}).get(
        "amaru-relay-1"
    ) or {})
    container = str(service.get("container") or "")
    if not container:
        raise MeasurementExecutionError("runtime has no Amaru relay container identity")

    def probe() -> dict[str, Any]:
        result = subprocess.run(
            ["docker", "logs", "--tail", "4000", container],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "cannot read Amaru logs")
        tip = extract_latest_adopted_tip(result.stdout + "\n" + result.stderr)
        if tip is None:
            raise RuntimeError("Amaru logs contain no adopted tip")
        return tip

    return probe


def _docker_cardano_tip_probe(runtime: dict[str, Any], node: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    container = str(node.get("container_name") or "")
    socket_path = str(node.get("container_socket_path") or "")
    network_magic = runtime.get("network_magic")
    if not container or not socket_path or not isinstance(network_magic, int):
        raise MeasurementExecutionError("runtime has no trusted Cardano-node tip-probe identity")

    def probe() -> dict[str, Any]:
        result = subprocess.run(
            [
                "docker", "exec", container, "cardano-cli", "query", "tip",
                "--socket-path", socket_path, "--testnet-magic", str(network_magic),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "cannot query Cardano-node tip")
        body = json.loads(result.stdout)
        height = body.get("block")
        if isinstance(height, bool) or not isinstance(height, int):
            raise RuntimeError("Cardano-node tip has no numeric block height")
        return {"block_height": height, "block_hash": body.get("hash")}

    return probe


@dataclass(frozen=True)
class PreparedScenarioMeasurements:
    resolution: dict[str, Any]
    runtime_metadata_path: Path
    target_node: str
    tip_probe: Callable[[], dict[str, Any]]
    implementation: str = "amaru"
    patched_trace_paths: tuple[Path, ...] = ()

    def build_factories(self, run_dir: str | Path) -> dict[str, Any]:
        run_path = Path(run_dir)
        if self.implementation == "cardano-node":
            traces = (
                run_path
                / "outputs"
                / "cardano-measurement-calibration"
                / "raw"
                / "node1.ndjson",
                run_path
                / "outputs"
                / "protocol-decode-cases"
                / "raw"
                / "node1.ndjson",
            )
            return build_cardano_measurement_factories(
                runtime_metadata_path=self.runtime_metadata_path,
                target_node=self.target_node,
                trace_paths=traces,
                tip_probe=self.tip_probe,
                peer_policy="three-node-controlled-local-mesh",
                allow_missing_trace_sources=True,
                patched_trace_paths=self.patched_trace_paths,
                target_identity=self.resolution["target_identity"],
            )
        traces = (
            run_path
            / "outputs"
            / "amaru-measurement-calibration"
            / "raw"
            / "amaru-relay-1.ndjson",
            run_path
            / "outputs"
            / "protocol-decode-cases"
            / "raw"
            / "amaru-relay-1.ndjson",
        )
        return build_amaru_measurement_factories(
            runtime_metadata_path=self.runtime_metadata_path,
            target_node=self.target_node,
            json_trace_paths=traces,
            otlp_trace_paths=[],
            tip_probe=self.tip_probe,
            peer_policy="single-controlled-producer",
            target_identity=self.resolution["target_identity"],
            allow_missing_trace_sources=True,
        )


def prepare_scenario_measurements(
    scenario,
    *,
    runtime_metadata_path: str | Path | None = None,
    tip_probe: Callable[[], dict[str, Any]] | None = None,
) -> PreparedScenarioMeasurements:
    """Resolve an explicit measurement selection against live real-node evidence."""
    implementation = scenario.target.get("implementation")
    if implementation not in {"amaru", "cardano-node"}:
        raise MeasurementExecutionError("runtime measurement binding is unsupported")
    if not scenario.profile:
        raise MeasurementExecutionError("measurements require a deployed profile")
    if runtime_metadata_path is None:
        from profile_manager.profiles import remote_base

        runtime_metadata_path = Path(remote_base()) / scenario.profile / "runtime.json"
    path = Path(runtime_metadata_path)
    try:
        runtime = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MeasurementExecutionError(
            f"fresh deployed runtime is unavailable: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MeasurementExecutionError(f"runtime metadata is invalid: {path}") from exc
    if implementation == "amaru":
        target_mode = str(
            (runtime.get("measurement_target") or {}).get("target_mode") or "stock"
        )
        if target_mode == "patched":
            identity = _patched_amaru_identity(runtime, scenario)
            capabilities = AMARU_PATCHED_CAPABILITIES
        elif target_mode == "stock":
            identity = _stock_amaru_identity(runtime, scenario)
            capabilities = AMARU_STOCK_CAPABILITIES
        else:
            raise MeasurementExecutionError(
                f"unsupported Amaru target mode: {target_mode}"
            )
        target_node = "amaru-relay-1"
        resolved_tip_probe = tip_probe or _docker_tip_probe(runtime)
    else:
        identity, node = _stock_cardano_identity(runtime, scenario)
        target_node = str(node.get("id") or "node1")
        resolved_tip_probe = tip_probe or _docker_cardano_tip_probe(runtime, node)
        capabilities = (
            CARDANO_PATCHED_CAPABILITIES
            if identity["mode"] == "patched"
            else CARDANO_STOCK_CAPABILITIES
        )
        patched_trace_paths: tuple[Path, ...] = ()
        if identity["mode"] == "patched":
            runtime_root = Path(str(runtime.get("runtime_root") or ""))
            if not runtime_root.is_absolute():
                raise MeasurementExecutionError(
                    "patched Cardano-node runtime root is not an absolute retained path"
                )
            patched_trace_paths = (
                runtime_root / "logs" / target_node / "cardano-measurement.ndjson",
                runtime_root / "logs" / target_node / "cardano-plutus.ndjson",
            )
    resolution = resolve_measurements(
        scenario,
        target_identity=identity,
        capabilities=capabilities,
    )
    return PreparedScenarioMeasurements(
        resolution=resolution,
        runtime_metadata_path=path,
        target_node=target_node,
        tip_probe=resolved_tip_probe,
        implementation=implementation,
        patched_trace_paths=(
            patched_trace_paths if implementation == "cardano-node" else ()
        ),
    )
