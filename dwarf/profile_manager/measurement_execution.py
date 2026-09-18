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


class MeasurementExecutionError(RuntimeError):
    """The selected measurements cannot be bound to proven runtime evidence."""


def _selected_release(runtime: dict[str, Any], implementation: str) -> dict[str, Any]:
    snapshot = ((runtime.get("versions") or {}).get("catalog_snapshot") or {})
    for release in snapshot.get("selected_releases") or []:
        if release.get("implementation") == implementation:
            return release
    raise MeasurementExecutionError(
        f"runtime catalog snapshot does not contain {implementation}"
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
        "version_catalog_revision": str(
            (runtime.get("versions") or {}).get("catalog_revision")
            or ((runtime.get("versions") or {}).get("catalog_snapshot") or {}).get(
                "catalog_revision"
            )
            or ""
        ),
    }


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


@dataclass(frozen=True)
class PreparedScenarioMeasurements:
    resolution: dict[str, Any]
    runtime_metadata_path: Path
    target_node: str
    tip_probe: Callable[[], dict[str, Any]]

    def build_factories(self, run_dir: str | Path) -> dict[str, Any]:
        run_path = Path(run_dir)
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
    """Resolve an explicit Amaru measurement selection against live evidence."""
    if scenario.target.get("implementation") != "amaru":
        raise MeasurementExecutionError("only Amaru runtime binding is implemented")
    if not scenario.profile:
        raise MeasurementExecutionError("Amaru measurements require a deployed profile")
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
    identity = _stock_amaru_identity(runtime, scenario)
    resolution = resolve_measurements(
        scenario,
        target_identity=identity,
        capabilities=AMARU_STOCK_CAPABILITIES,
    )
    return PreparedScenarioMeasurements(
        resolution=resolution,
        runtime_metadata_path=path,
        target_node="amaru-relay-1",
        tip_probe=tip_probe or _docker_tip_probe(runtime),
    )
