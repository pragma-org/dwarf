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
from profile_manager.measurement_collectors.cardano_factory import (
    build_cardano_measurement_factories,
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
    if observed_digest != digest:
        raise MeasurementExecutionError("deployed Cardano-node image digest does not match catalog")
    if str(node.get("source_revision") or "") != source_revision:
        raise MeasurementExecutionError("deployed Cardano-node source revision does not match catalog")
    image_reference = str(node.get("image_ref") or node.get("image") or "")
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

    def build_factories(self, run_dir: str | Path) -> dict[str, Any]:
        run_path = Path(run_dir)
        if self.implementation == "cardano-node":
            trace = (
                run_path
                / "outputs"
                / "cardano-measurement-calibration"
                / "raw"
                / "node1.ndjson"
            )
            return build_cardano_measurement_factories(
                runtime_metadata_path=self.runtime_metadata_path,
                target_node=self.target_node,
                trace_paths=[trace],
                tip_probe=self.tip_probe,
                peer_policy="three-node-controlled-local-mesh",
                allow_missing_trace_sources=True,
            )
        trace = (
            run_path
            / "outputs"
            / "amaru-measurement-calibration"
            / "raw"
            / "amaru-relay-1.ndjson"
        )
        return build_amaru_measurement_factories(
            runtime_metadata_path=self.runtime_metadata_path,
            target_node=self.target_node,
            json_trace_paths=[trace],
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
        identity = _stock_amaru_identity(runtime, scenario)
        target_node = "amaru-relay-1"
        resolved_tip_probe = tip_probe or _docker_tip_probe(runtime)
        capabilities = AMARU_STOCK_CAPABILITIES
    else:
        identity, node = _stock_cardano_identity(runtime, scenario)
        target_node = str(node.get("id") or "node1")
        resolved_tip_probe = tip_probe or _docker_cardano_tip_probe(runtime, node)
        capabilities = CARDANO_STOCK_CAPABILITIES
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
    )
