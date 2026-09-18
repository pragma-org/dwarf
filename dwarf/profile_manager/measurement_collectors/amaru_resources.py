"""Run-scoped resource measurements for the actual Amaru process."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from profile_manager.measurement_report import distribution_summary
from scripts.runtime_resource_profile import collect_samples, resolve_target_pid


def _distribution(samples: list[dict[str, Any]], field: str, unit: str) -> dict[str, Any]:
    return distribution_summary(
        [
            {"value": sample.get(field), "unit": unit}
            for sample in samples
            if sample.get(field) is not None
        ],
        unit=unit,
    )


def _counter_delta(
    samples: list[dict[str, Any]], field: str, unit: str, *, scope: str | None = None
) -> dict[str, Any]:
    points = [
        (sample.get("monotonic_seconds"), sample.get(field))
        for sample in samples
        if sample.get("monotonic_seconds") is not None and sample.get(field) is not None
    ]
    if len(points) < 2:
        return {
            "status": "unavailable",
            "unit": unit,
            "start": None,
            "end": None,
            "delta": None,
            "rate_per_second": None,
            **({"scope": scope} if scope else {}),
            "reason": "at least two monotonic counter samples are required",
        }
    start_time, start = points[0]
    end_time, end = points[-1]
    duration = end_time - start_time
    delta = end - start
    valid = duration > 0 and delta >= 0
    return {
        "status": "available" if valid else "unavailable",
        "unit": unit,
        "start": start,
        "end": end,
        "delta": delta if valid else None,
        "duration_seconds": duration,
        "rate_per_second": delta / duration if valid else None,
        **({"scope": scope} if scope else {}),
        **({} if valid else {"reason": "counter reset or non-positive sample duration"}),
    }


def _cpu_percent_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = []
    previous = None
    for sample in samples:
        point = (sample.get("monotonic_seconds"), sample.get("cpu_time_seconds"))
        if point[0] is None or point[1] is None:
            continue
        if previous is not None:
            elapsed = point[0] - previous[0]
            cpu_delta = point[1] - previous[1]
            if elapsed > 0 and cpu_delta >= 0:
                values.append({"value": cpu_delta / elapsed * 100, "unit": "%"})
        previous = point
    return values


class AmaruResourceCollector:
    """Sample Linux process counters for the resolved Amaru child process."""

    def __init__(
        self,
        entry: dict[str, Any],
        *,
        runtime_metadata_path: str | Path,
        target_node: str,
        sample_interval_seconds: float = 1.0,
        resolve_pid: Callable[[Path, str], int] = resolve_target_pid,
        sample_reader: Callable[[int, int], dict[str, Any]] | None = None,
        background: bool = True,
    ) -> None:
        if sample_interval_seconds <= 0 or sample_interval_seconds > 60:
            raise ValueError("sample_interval_seconds must be within (0, 60]")
        self.entry = entry
        self.runtime_metadata_path = Path(runtime_metadata_path)
        self.target_node = target_node
        self.sample_interval_seconds = sample_interval_seconds
        self._resolve_pid = resolve_pid
        self._sample_reader = sample_reader
        self._background = background
        self._pid: int | None = None
        self._samples: list[dict[str, Any]] = []
        self._sample_errors: list[str] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def prepare(self, context) -> None:
        if not self.runtime_metadata_path.is_file():
            raise FileNotFoundError(
                f"runtime metadata is unavailable: {self.runtime_metadata_path}"
            )
        context.collector_dir.mkdir(parents=True, exist_ok=True)
        self._pid = int(self._resolve_pid(self.runtime_metadata_path, self.target_node))

    def _read_sample(self, index: int) -> dict[str, Any]:
        if self._pid is None:
            raise RuntimeError("Amaru resource collector was not prepared")
        if self._sample_reader is not None:
            sample = self._sample_reader(self._pid, index)
        else:
            sample = collect_samples(
                pid=self._pid,
                sample_count=1,
                sample_interval_seconds=0,
            )[0]
        return {**sample, "sample_index": index, "target_node": self.target_node}

    def _capture(self) -> None:
        with self._lock:
            index = len(self._samples)
        try:
            sample = self._read_sample(index)
        except Exception as exc:  # optional telemetry must not stop the scenario
            with self._lock:
                self._sample_errors.append(f"{type(exc).__name__}: {exc}")
            return
        with self._lock:
            self._samples.append(sample)

    def _sample_loop(self) -> None:
        while not self._stop_event.wait(self.sample_interval_seconds):
            self._capture()

    def start(self, context) -> None:
        self._capture()
        if self._background:
            self._thread = threading.Thread(
                target=self._sample_loop,
                name=f"dwarf-resource-{self.target_node}",
                daemon=True,
            )
            self._thread.start()

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.sample_interval_seconds * 2))
        self._capture()

    def finalize(self, context) -> dict[str, Any]:
        with self._lock:
            samples = list(self._samples)
            sample_errors = list(self._sample_errors)
        network_scopes = sorted(
            {
                str(sample["network_scope"])
                for sample in samples
                if sample.get("network_scope") not in {None, "unavailable"}
            }
        )
        network_scope = network_scopes[0] if len(network_scopes) == 1 else (
            "mixed" if network_scopes else "unavailable"
        )
        measurements = {
            "cpu_percent": distribution_summary(_cpu_percent_samples(samples), unit="%"),
            "cpu_time_seconds": _counter_delta(samples, "cpu_time_seconds", "s"),
            "rss_bytes": _distribution(samples, "rss_bytes", "bytes"),
            "network_rx_bytes": _counter_delta(
                samples, "network_rx_bytes", "bytes", scope=network_scope
            ),
            "network_tx_bytes": _counter_delta(
                samples, "network_tx_bytes", "bytes", scope=network_scope
            ),
            "disk_read_bytes": _counter_delta(samples, "disk_read_bytes", "bytes"),
            "disk_write_bytes": _counter_delta(samples, "disk_write_bytes", "bytes"),
            "fd_count": _distribution(samples, "fd_count", "fds"),
            "threads": _distribution(samples, "threads", "threads"),
        }
        result = {
            "schema_version": "v1",
            "measurement_id": self.entry["id"],
            "target": {"node": self.target_node, "pid": self._pid},
            "measurements": measurements,
            "source": {
                "kind": "linux-proc",
                "sample_count": len(samples),
                "sample_interval_seconds": self.sample_interval_seconds,
                "errors": sample_errors,
            },
        }
        context.write_json("samples.json", samples)
        context.write_json("result.json", result)
        return result
