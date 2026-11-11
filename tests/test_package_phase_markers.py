"""F185. Inside an Antithesis package the workload has no MeasurementRuntime (the dwarf-measurements
service collects on its own), so every window primitive raised "active measurement runtime is
unavailable". A phase-marker proxy gives the package the same `measurements/windows.ndjson` the
local path writes, and announces each marker as an SDK event so the platform export carries it."""
from __future__ import annotations

import json
from types import SimpleNamespace

from profile_manager import primitives, scenario
from profile_manager.measurement_runtime import PackagePhaseMarkers


def test_package_phase_markers_write_windows_and_announce_them(tmp_path):
    env = {"ANTITHESIS_OUTPUT_DIR": str(tmp_path / "out")}
    proxy = PackagePhaseMarkers(run_dir=tmp_path / "run", environ=env)
    handle = SimpleNamespace(_measurement_runtime=proxy)
    assert primitives._active_measurement_runtime(handle) is proxy
    start = proxy.mark_phase("baseline", "start")
    end = proxy.mark_phase("baseline", "end", phase_index=2)
    rows = [json.loads(l) for l in (tmp_path / "run" / "measurements" / "windows.ndjson").read_text().splitlines()]
    assert [r["phase_id"] for r in rows] == ["baseline", "baseline"] and [r["state"] for r in rows] == ["start", "end"]
    assert rows[1]["phase_index"] == 2 and 0 <= rows[0]["elapsed_seconds"] <= rows[1]["elapsed_seconds"]
    assert start["epoch_seconds"] <= end["epoch_seconds"] and proxy.elapsed_seconds() >= rows[1]["elapsed_seconds"]
    lines = [json.loads(l) for l in (tmp_path / "out" / "sdk.jsonl").read_text().splitlines()]
    events = [l["antithesis_assert"] for l in lines if l.get("antithesis_assert", {}).get("id") == "dwarf_measurement_window"]
    assert [e["details"]["marker"]["state"] for e in events] == ["start", "end"]


def test_the_runner_attaches_the_proxy_only_for_the_antithesis_backend_without_a_context(tmp_path):
    attach = scenario._package_phase_markers
    assert isinstance(attach(backend="antithesis", measurement_context=None, run_dir=tmp_path), PackagePhaseMarkers)
    assert attach(backend="local", measurement_context=None, run_dir=tmp_path) is None
    assert attach(backend="antithesis", measurement_context={"taps": []}, run_dir=tmp_path) is None
