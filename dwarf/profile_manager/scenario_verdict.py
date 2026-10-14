"""One fail-closed verdict contract for every DWARF scenario entry point."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


TEST_RUN_KINDS = frozenset({"scenario", "ci", "external"})


@dataclass(frozen=True)
class ScenarioVerdict:
    operational_outcome: str
    security_verdict: str | None
    reason_codes: tuple[str, ...]
    measurement_failures: tuple[str, ...] = ()


def _assertion_counts(assertions: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None):
    if assertions is None:
        return 0, 0
    if isinstance(assertions, Mapping):
        return int(assertions.get("pass", 0) or 0), int(assertions.get("fail", 0) or 0)
    passed = sum(item.get("result") == "pass" for item in assertions)
    failed = sum(item.get("result") == "fail" for item in assertions)
    return passed, failed


def _required_measurement_failures(measurements: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(measurements, Mapping):
        return ()
    resolution = measurements.get("resolution")
    resolved = resolution.get("resolved", []) if isinstance(resolution, Mapping) else []
    states = measurements.get("collector_states") or {}
    results = measurements.get("collector_results") or {}
    gates = measurements.get("threshold_gates") or {}
    required_entries: dict[str, Mapping[str, Any]] = {}
    for entry in resolved:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str):
            continue
        measurement_id = entry["id"]
        threshold = entry.get("threshold_gate") or {}
        gated = bool(isinstance(threshold, Mapping) and threshold.get("enabled"))
        required = gated or entry.get("required") is True
        if not required:
            continue
        required_entries[measurement_id] = entry
    for measurement_id, gate in gates.items():
        if isinstance(measurement_id, str) and isinstance(gate, Mapping) and gate.get("enabled"):
            required_entries.setdefault(measurement_id, {"id": measurement_id, "threshold_gate": gate})

    failures: list[str] = []
    for measurement_id, entry in required_entries.items():
        threshold = entry.get("threshold_gate") or {}
        runtime_gate = gates.get(measurement_id) or {}
        gated = bool(
            (isinstance(threshold, Mapping) and threshold.get("enabled"))
            or (isinstance(runtime_gate, Mapping) and runtime_gate.get("enabled"))
        )
        state_valid = states.get(measurement_id) == "finalized"
        if gated:
            evidence_valid = (
                runtime_gate.get("enabled") is True
                and runtime_gate.get("result") == "pass"
            )
        else:
            evidence_valid = measurement_id in results and bool(results.get(measurement_id))
        if not state_valid or not evidence_valid:
            failures.append(measurement_id)
    if measurements.get("gate_failed") and not failures:
        failures.append("measurement-gate")
    return tuple(sorted(set(failures)))


def evaluate_scenario_verdict(
    *,
    exit_status: str,
    assertions: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    measurements: Mapping[str, Any] | None,
    run_kind: str,
) -> ScenarioVerdict:
    """Keep operational completion separate from a non-vacuous test verdict."""
    if run_kind not in TEST_RUN_KINDS:
        return ScenarioVerdict(exit_status, None, ())

    passed, failed = _assertion_counts(assertions)
    reasons: list[str] = []
    if exit_status != "pass":
        reasons.append("operational-outcome-not-pass")
    if failed:
        reasons.append("assertions-failed")
    if passed <= 0:
        reasons.append("no-passing-assertions")
    measurement_failures = _required_measurement_failures(measurements)
    if measurement_failures:
        reasons.append("required-measurement-evidence-missing")
    return ScenarioVerdict(
        operational_outcome=exit_status,
        security_verdict="fail" if reasons else "pass",
        reason_codes=tuple(reasons),
        measurement_failures=measurement_failures,
    )
