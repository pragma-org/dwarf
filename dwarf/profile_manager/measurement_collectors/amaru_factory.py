"""Trusted runtime bindings for the version-pinned Amaru collectors."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from profile_manager.measurement_collectors.amaru_external import (
    RestartReadinessCollector,
    SyncSpeedCollector,
    WorkloadAccountingCollector,
)
from profile_manager.measurement_collectors.amaru_resources import AmaruResourceCollector
from profile_manager.measurement_collectors.amaru_stock import build_amaru_stock_factories


def build_amaru_measurement_factories(
    *,
    runtime_metadata_path: str | Path,
    target_node: str,
    json_trace_paths: Iterable[str | Path],
    otlp_trace_paths: Iterable[str | Path],
    tip_probe: Callable[[], dict[str, Any]] | None,
    expected_start_height: int | None = None,
    expected_end_height: int | None = None,
    peer_policy: str = "unspecified",
    resource_sample_interval_seconds: float = 1.0,
    resource_resolve_pid: Callable[[Path, str], int] | None = None,
    resource_sample_reader: Callable[[int, int], dict[str, Any]] | None = None,
    resource_background: bool = True,
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Bind deployment-owned paths and probes; scenario parameters cannot replace them."""
    metadata_path = Path(runtime_metadata_path)
    factories = build_amaru_stock_factories(
        json_trace_paths=json_trace_paths,
        otlp_trace_paths=otlp_trace_paths,
    )

    resource_options: dict[str, Any] = {
        "runtime_metadata_path": metadata_path,
        "target_node": target_node,
        "sample_interval_seconds": resource_sample_interval_seconds,
        "sample_reader": resource_sample_reader,
        "background": resource_background,
    }
    if resource_resolve_pid is not None:
        resource_options["resolve_pid"] = resource_resolve_pid

    factories["amaru-stock-resources"] = lambda entry: AmaruResourceCollector(
        entry, **resource_options
    )
    factories["amaru-external-workload-accounting"] = (
        lambda entry: WorkloadAccountingCollector(entry)
    )
    factories["amaru-external-restart-readiness"] = (
        lambda entry: RestartReadinessCollector(entry, target_node=target_node)
    )
    if tip_probe is not None:
        factories["amaru-external-sync-speed"] = lambda entry: SyncSpeedCollector(
            entry,
            tip_probe=tip_probe,
            expected_start_height=expected_start_height,
            expected_end_height=expected_end_height,
            peer_policy=peer_policy,
        )
    return factories
