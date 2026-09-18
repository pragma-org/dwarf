"""Isolated lifecycle manager for optional, version-pinned measurements."""
from __future__ import annotations

import json
import math
import operator
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping


class MeasurementArtifactPathError(ValueError):
    """Raised when a collector attempts to write outside its evidence root."""


@dataclass(frozen=True)
class CollectorContext:
    measurement_id: str
    definition: dict[str, Any]
    parameters: dict[str, Any]
    run_dir: Path
    collector_dir: Path

    def artifact_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path:
            raise MeasurementArtifactPathError("collector artifact path is required")
        posix = PurePosixPath(relative_path)
        if posix.is_absolute() or ".." in posix.parts or "\\" in relative_path:
            raise MeasurementArtifactPathError(
                f"collector artifact path escapes its evidence root: {relative_path}"
            )
        destination = self.collector_dir.joinpath(*posix.parts)
        try:
            destination.resolve().relative_to(self.collector_dir.resolve())
        except ValueError as exc:
            raise MeasurementArtifactPathError(
                f"collector artifact path escapes its evidence root: {relative_path}"
            ) from exc
        return destination

    def write_json(self, relative_path: str, payload: Any) -> str:
        destination = self.artifact_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return destination.relative_to(self.run_dir).as_posix()


@dataclass
class _CollectorRecord:
    entry: dict[str, Any]
    collector: Any
    context: CollectorContext
    state: str = "selected"
    start_attempted: bool = False


class MeasurementRuntime:
    """Run selected measurement taps without changing scenario semantics by default."""

    def __init__(
        self,
        *,
        run_dir: str | Path,
        resolution: dict[str, Any],
        collector_factories: Mapping[str, Callable[[dict[str, Any]], Any]],
        scenario_id: str | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.measurements_dir = self.run_dir / "measurements"
        self.collectors_dir = self.measurements_dir / "collectors"
        self._resolution = json.loads(json.dumps(resolution))
        self._scenario_id = scenario_id
        self._factories = dict(collector_factories)
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._run_started_mono: float | None = None
        self._records: dict[str, _CollectorRecord] = {}
        self._errors: list[dict[str, Any]] = []
        self._results: dict[str, dict[str, Any]] = {}
        self._threshold_gates: dict[str, dict[str, Any]] = {}
        self._prepared = False
        self._started = False
        self._finalized: dict[str, Any] | None = None
        self._duration_seconds: float | None = None

        for entry in self._resolution.get("resolved", []):
            measurement_id = entry["id"]
            factory = self._factories.get(measurement_id)
            if factory is None:
                self._errors.append(
                    {
                        "measurement_id": measurement_id,
                        "phase": "construct",
                        "type": "CollectorUnavailable",
                        "message": "no collector factory is registered",
                    }
                )
                continue
            collector_dir = self.collectors_dir / measurement_id
            context = CollectorContext(
                measurement_id=measurement_id,
                definition=dict(entry.get("definition") or {}),
                parameters=dict(entry.get("parameters") or {}),
                run_dir=self.run_dir,
                collector_dir=collector_dir,
            )
            try:
                collector = factory(entry)
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._errors.append(self._error(measurement_id, "construct", exc))
                continue
            self._records[measurement_id] = _CollectorRecord(
                entry=entry, collector=collector, context=context
            )

    @staticmethod
    def _error(measurement_id: str, phase: str, exc: Exception) -> dict[str, Any]:
        return {
            "measurement_id": measurement_id,
            "phase": phase,
            "type": type(exc).__name__,
            "message": str(exc),
        }

    def _record_error(self, record: _CollectorRecord, phase: str, exc: Exception) -> None:
        record.state = "error"
        self._errors.append(self._error(record.entry["id"], phase, exc))

    def _write_marker(
        self, phase_id: str, state: str, *, phase_index: int | None = None
    ) -> dict[str, Any]:
        now_wall = float(self._wall_clock())
        now_mono = float(self._monotonic_clock())
        if self._run_started_mono is None:
            self._run_started_mono = now_mono
        marker = {
            "phase_id": phase_id,
            "state": state,
            "epoch_seconds": now_wall,
            "elapsed_seconds": round(now_mono - self._run_started_mono, 9),
        }
        if phase_index is not None:
            marker["phase_index"] = phase_index
        self.measurements_dir.mkdir(parents=True, exist_ok=True)
        with (self.measurements_dir / "windows.ndjson").open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(marker, sort_keys=True) + "\n")
        return marker

    def prepare(self) -> None:
        if self._prepared:
            return
        self.collectors_dir.mkdir(parents=True, exist_ok=True)
        self._write_marker("run", "start")
        for record in self._records.values():
            record.context.collector_dir.mkdir(parents=True, exist_ok=True)
            try:
                record.collector.prepare(record.context)
                record.state = "prepared"
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "prepare", exc)
        self._prepared = True

    def start(self) -> None:
        if self._started:
            return
        if not self._prepared:
            self.prepare()
        for record in self._records.values():
            if record.state != "prepared":
                continue
            record.start_attempted = True
            try:
                record.collector.start(record.context)
                record.state = "started"
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "start", exc)
        self._started = True

    def mark_phase(
        self, phase_id: str, state: str, *, phase_index: int | None = None
    ) -> dict[str, Any]:
        if state not in {"start", "end"}:
            raise ValueError("measurement phase marker state must be start or end")
        marker = self._write_marker(phase_id, state, phase_index=phase_index)
        for record in self._records.values():
            if record.state != "started":
                continue
            try:
                record.collector.on_marker(marker, record.context)
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "marker", exc)
        return marker

    def _stop(self) -> None:
        if not self._started:
            return
        marker = self._write_marker("run", "end")
        self._duration_seconds = marker["elapsed_seconds"]
        for record in self._records.values():
            if record.state == "started":
                try:
                    record.collector.on_marker(marker, record.context)
                except Exception as exc:  # noqa: BLE001
                    self._record_error(record, "marker", exc)
            if not record.start_attempted:
                continue
            try:
                record.collector.stop(record.context)
                if record.state != "error":
                    record.state = "stopped"
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "stop", exc)
        self._started = False

    @staticmethod
    def _metric_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        mapped = {}
        for metric in result.get("metrics") or []:
            if isinstance(metric, dict) and isinstance(metric.get("name"), str):
                mapped[metric["name"]] = metric
        measurements = result.get("measurements")
        if isinstance(measurements, dict):
            for name, metric in measurements.items():
                if isinstance(name, str) and isinstance(metric, dict):
                    mapped[name] = {"name": name, **metric}
        return mapped

    @staticmethod
    def _evaluate_threshold(actual: Any, op: str, expected: Any) -> bool:
        operations = {
            "gte": operator.ge,
            "gt": operator.gt,
            "lte": operator.le,
            "lt": operator.lt,
            "eq": operator.eq,
        }
        if op not in operations:
            return False
        if isinstance(actual, float) and not math.isfinite(actual):
            return False
        try:
            return bool(operations[op](actual, expected))
        except (TypeError, ValueError):
            return False

    def _evaluate_gate(
        self, record: _CollectorRecord, result: dict[str, Any]
    ) -> dict[str, Any]:
        gate = record.entry.get("threshold_gate") or {
            "enabled": False,
            "thresholds": [],
        }
        if not gate.get("enabled"):
            return {"enabled": False, "result": "not-gated", "checks": []}
        metrics = self._metric_map(result)
        checks = []
        for threshold in gate.get("thresholds") or []:
            name = threshold.get("metric")
            metric = metrics.get(name)
            actual = metric.get("value") if metric is not None else None
            unit = metric.get("unit") if metric is not None else None
            expected_unit = threshold.get("unit")
            passed = (
                metric is not None
                and (expected_unit is None or unit == expected_unit)
                and self._evaluate_threshold(
                    actual, threshold.get("operator"), threshold.get("value")
                )
            )
            checks.append(
                {
                    "metric": name,
                    "operator": threshold.get("operator"),
                    "expected": threshold.get("value"),
                    "unit": expected_unit,
                    "actual": actual,
                    "actual_unit": unit,
                    "result": "pass" if passed else "fail",
                }
            )
        return {
            "enabled": True,
            "result": "pass" if checks and all(c["result"] == "pass" for c in checks) else "fail",
            "checks": checks,
        }

    def finalize(self) -> dict[str, Any]:
        if self._finalized is not None:
            return json.loads(json.dumps(self._finalized))
        self._stop()
        for measurement_id, record in self._records.items():
            try:
                result = record.collector.finalize(record.context) or {}
                if not isinstance(result, dict):
                    raise TypeError("collector finalize result must be an object")
                self._results[measurement_id] = result
                if record.state != "error":
                    record.state = "finalized"
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "finalize", exc)
                result = {}
            self._threshold_gates[measurement_id] = self._evaluate_gate(record, result)
        for entry in self._resolution.get("resolved", []):
            measurement_id = entry["id"]
            if measurement_id in self._threshold_gates:
                continue
            gate = entry.get("threshold_gate") or {
                "enabled": False,
                "thresholds": [],
            }
            if not gate.get("enabled"):
                self._threshold_gates[measurement_id] = {
                    "enabled": False,
                    "result": "not-gated",
                    "checks": [],
                }
                continue
            self._threshold_gates[measurement_id] = {
                "enabled": True,
                "result": "fail",
                "checks": [
                    {
                        "metric": threshold.get("metric"),
                        "operator": threshold.get("operator"),
                        "expected": threshold.get("value"),
                        "unit": threshold.get("unit"),
                        "actual": None,
                        "actual_unit": None,
                        "result": "fail",
                    }
                    for threshold in gate.get("thresholds") or []
                ],
            }
        from profile_manager.measurement_report import write_measurement_reports

        measurements: dict[str, Any] = {}
        for measurement_id, collector_result in self._results.items():
            for name, summary in (collector_result.get("measurements") or {}).items():
                key = name if name not in measurements else f"{measurement_id}.{name}"
                measurements[key] = summary
        report = {
            "schema_version": "v1",
            "scenario": self._scenario_id,
            "duration_seconds": self._duration_seconds,
            "target_identity": self._resolution.get("target_identity"),
            "measurements": measurements,
        }
        report_artifacts = write_measurement_reports(self.run_dir, report)
        snapshot = self.snapshot()
        snapshot["report_artifacts"] = report_artifacts
        snapshot["gate_failed"] = any(
            gate["enabled"] and gate["result"] == "fail"
            for gate in self._threshold_gates.values()
        )
        (self.measurements_dir / "runtime.json").write_text(
            json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._finalized = snapshot
        return json.loads(json.dumps(snapshot))

    def snapshot(self) -> dict[str, Any]:
        states = {
            entry["id"]: (
                self._records[entry["id"]].state
                if entry["id"] in self._records
                else "error"
            )
            for entry in self._resolution.get("resolved", [])
        }
        return {
            "resolution": json.loads(json.dumps(self._resolution)),
            "collector_states": states,
            "collector_errors": json.loads(json.dumps(self._errors)),
            "collector_results": json.loads(json.dumps(self._results)),
            "threshold_gates": json.loads(json.dumps(self._threshold_gates)),
            "gate_failed": False,
        }

    def scenario_exit_status(self, scenario_status: str) -> str:
        result = self._finalized or self.snapshot()
        if scenario_status == "pass" and result.get("gate_failed"):
            return "fail"
        return scenario_status
