"""Pure, conservative evaluation of retained campaign evidence.

``evaluate_campaign_evidence(plan, evidence)`` accepts a frozen campaign plan and
an explicit evidence index (schema_version, campaign_digest, builds, security,
measurements). It does not discover runs or infer success from exit status.

Build observations bind mode/source/artifact identities. Security rows bind an
entry, scenario/contract digests, targets, a normal_control, native assertions,
reachability and oracle observations. Observations carry run_id, node_id,
boundary, input_digest and diagnostic; reachability also has activity_count.
The boundary is the retained runner's explicit interpretation of the frozen
prose contract, not an interpretation invented by this evaluator.

Measurement rows bind topology_id/node_id/measurement_id/mode, candidate and
scenario_digest, normal_control, and the existing per-tap ``result``. Selected
raw ``samples`` and ``events`` are separate from aggregate result.records.
Events may use native event/kind and payload/fields/correlation dictionaries.
Progress uses start_block/end_block; result.coverage uses covered_functions and
paths. All exercise predicates are conjunctive. Unrelated log rows should not
be indexed as evidence for a cell. No metrics, thresholds or free text are
silently translated into security assertions.

Missing observations produce inconclusive/incomplete results. Malformed types,
ambiguous duplicate bindings, unknown cells and tampered identities raise
ValueError. These checks establish evidence validity, not authenticity: callers
must verify retained run files/build attestations before supplying the index.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator

from profile_manager.run_ledger import atomic_write_bytes
from profile_manager.security_campaign import _check_pair, _digest, _pair_status, _schema_validate, digest_security_campaign, serialize_campaign

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MODES = {"stock", "patched", "coverage"}
_STATES = {"available", "collected", "verified", "exercised", "unavailable", "failed", "blocked", "reserved"}
_PRECEDENCE = ("harness-invalid", "infrastructure-invalid", "finding", "inconclusive", "pass")
_SUITE_SCHEMA = Path(__file__).resolve().parents[1] / "spec/v1/security-campaign-suite.schema.json"


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _rows(value: Any, label: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{label} must be an array of objects")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _assertion_nonvacuous(assertion: dict) -> bool:
    """Validate native points without confusing failure-only lists with activity.

    Fuzz and load-event primitives retain only failures in data_points_used.
    Their evaluated counters establish the exercised population, including
    successful zero-failure runs. Other assertions need a nonempty evidence list;
    legacy numeric counts remain readable but cannot override supplied native
    counters. Unknown empty-list semantics are deliberately inconclusive.
    """
    points = assertion.get("data_points_used", 0)
    numeric = not isinstance(points, list)
    if numeric:
        if _count(points, "assertion.data_points_used") == 0:
            return False
    else:
        points = _rows(points, "assertion.data_points_used")
        if any(not point or not any(value is not None for value in point.values()) for point in points):
            return False
    primitive = assertion.get("primitive")
    if (primitive not in {"parse_succeeds_or_clean_error", "roundtrip_equals_original", "load_events_are_ok"}
            or numeric and not isinstance(assertion.get("evaluated_value"), dict)):
        return numeric or bool(points)
    evaluated = _object(assertion.get("evaluated_value"), "assertion.evaluated_value")
    params = _object(assertion.get("params", {}), "assertion.params")
    if primitive == "load_events_are_ok":
        counts = {key: _count(evaluated.get(key), f"assertion.evaluated_value.{key}")
                  for key in ("completed", "non_ok", "min_completed", "min_event_count", "required_completed")}
        minimum = _count(params.get("min_completed", 1), "assertion.params.min_completed")
        event_count = _count(params.get("min_event_count", 2), "assertion.params.min_event_count")
        required = max(minimum, event_count)
        return (required >= 1 and counts["min_completed"] == minimum
                and counts["min_event_count"] == event_count and counts["required_completed"] == required
                and counts["completed"] >= required and counts["non_ok"] <= counts["completed"]
                and (numeric or len(points) == counts["non_ok"]
                     and all(isinstance(point.get("outcome"), str) and point["outcome"].strip()
                             and point["outcome"] != "ok" for point in points))
                and assertion.get("result") == ("fail" if counts["non_ok"] else "pass"))
    if primitive == "parse_succeeds_or_clean_error":
        counts = {key: _count(evaluated.get(key), f"assertion.evaluated_value.{key}")
                  for key in ("ok", "clean_error", "crash", "total_outcomes", "min_outcomes_count")}
        population = counts["total_outcomes"]
        failures = counts["crash"]
        minimum_key = "min_outcomes_count"
        consistent = population == counts["ok"] + counts["clean_error"] + failures
    else:
        counts = {key: _count(evaluated.get(key), f"assertion.evaluated_value.{key}")
                  for key in ("ok", "matched", "mismatched", "min_inputs_parsed")}
        population = counts["ok"]
        failures = counts["mismatched"]
        minimum_key = "min_inputs_parsed"
        consistent = population == counts["matched"] + failures
    minimum = _count(params.get(minimum_key, 1), f"assertion.params.{minimum_key}")
    expected_result = "fail" if failures else "pass"
    return (consistent and minimum >= 1 and counts[minimum_key] == minimum
            and population >= minimum and (numeric or len(points) == failures)
            and assertion.get("result") == expected_result)


def _immutable(value: Any) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


def _index(rows: list[dict], fields: tuple[str, ...], label: str) -> dict:
    indexed = {}
    for row in rows:
        key = tuple(_text(row.get(field), f"{label}.{field}") for field in fields)
        if key in indexed:
            raise ValueError(f"duplicate {label} binding: {key}")
        indexed[key] = row
    return indexed


def _validate_references(manifest: dict) -> None:
    """Validate links and rule types that the plan's storage schema leaves open."""
    topologies = _index(manifest["topologies"], ("id",), "frozen topology")
    _index(manifest["entries"], ("id",), "frozen entry")
    variants = _index(manifest["build_variants"], ("mode",), "frozen build variant")
    if set(variants) != {(mode,) for mode in _MODES}:
        raise ValueError("frozen manifest requires stock, patched and coverage build variants")
    definitions = manifest["definitions"]
    scenarios = definitions["scenarios"]
    for entry in manifest["entries"]:
        contract = entry["contract"]
        if (entry["topology_id"],) not in topologies:
            raise ValueError(f"unknown frozen entry topology: {entry['topology_id']}")
        for scenario_id in (contract.get("scenario_id"), _object(contract.get("normal_control"), "contract.normal_control").get("scenario_id")):
            if scenario_id not in scenarios:
                raise ValueError(f"missing frozen scenario definition: {scenario_id}")
        targets = contract.get("target_nodes")
        nodes = {node["node_id"] for node in topologies[(entry["topology_id"],)]["nodes"]}
        if not isinstance(targets, list) or any(not isinstance(target, str) or target not in nodes for target in targets) or not targets:
            raise ValueError("frozen security target_nodes must reference topology nodes")
    rule_schema = json.loads(_SUITE_SCHEMA.read_text(encoding="utf-8"))["properties"]["exercises"]["items"]["properties"]["rule"]
    rule_validator = Draft202012Validator(rule_schema)
    for cell in manifest["required_tap_cells"]:
        topology = topologies.get((cell["topology_id"],))
        if topology is None or cell["node_id"] not in {node["node_id"] for node in topology["nodes"]}:
            raise ValueError("frozen measurement cell references an unknown topology/node")
        if cell["scenario_id"] not in scenarios or cell["measurement_id"] not in definitions["measurements"]:
            raise ValueError("frozen measurement cell references a missing scenario/tap definition")
        errors = list(rule_validator.iter_errors(cell["rule"]))
        if errors:
            raise ValueError(f"invalid frozen measurement exercise rule: {errors[0].message}")


def _target_errors(target: Any, node: dict, mode: str, manifest: dict, builds: dict, *, library=False) -> list[str]:
    if target is None:
        return [f"missing target identity for {node['node_id']}"]
    target = _object(target, "target")
    identity = node["identity"]
    expected = {key: identity[key] for key in ("implementation", "version", "source_revision")}
    expected["mode"] = mode
    reasons = [f"target {key} differs for {node['node_id']}" for key, value in expected.items() if target.get(key) != value]
    artifacts = identity["artifacts"]
    if identity["kind"] == "candidate":
        build = builds.get((mode,))
        if build is None:
            return reasons + [f"missing {mode} candidate build observation"]
        if build.get("source") != manifest["candidate"]:
            reasons.append("build source differs from frozen candidate")
        if target.get("source_tree_digest", identity["source_tree_digest"]) != identity["source_tree_digest"]:
            reasons.append("target source tree differs from frozen candidate")
        artifacts = _object(build.get("artifacts"), "build.artifacts")
        variant = next(v for v in manifest["build_variants"] if v["mode"] == mode)
        for key, declared in variant["artifacts"].items():
            if declared is not None and artifacts.get(key) != declared:
                reasons.append(f"build {key} differs from frozen expectation")
        for key in ("patch_set_digest", "instrumentation_digest", "toolchain_digest"):
            if variant.get(key) is not None and build.get(key) != variant[key]:
                reasons.append(f"build {key} differs from frozen expectation")
        if mode != "stock":
            key = "patch_set_digest" if mode == "patched" else "instrumentation_digest"
            field = "patch_set_sha256" if mode == "patched" else "coverage_harness_sha256"
            if not _immutable(build.get(key)) or target.get(field) != str(build.get(key)).removeprefix("sha256:"):
                reasons.append(f"missing or mismatched {mode} instrumentation identity")
    elif mode != "stock":
        reasons.append("release instrumentation build identity is not frozen in this campaign")
    library = library and target.get("runtime") == "library"
    if library:
        if not _immutable(target.get("library_manifest_digest")) or not target.get("target_id"):
            reasons.append("library target requires a frozen manifest identity")
        declaration = (builds.get((mode,), {}).get("library_targets") or {}).get(target.get("target_id"), {})
        if declaration:
            artifacts = {**artifacts, "executable_digest": declaration.get("executable_digest"),
                         "binary_digest": declaration.get("executable_digest")}
            if declaration.get("manifest_digest", target.get("library_manifest_digest")) != target.get("library_manifest_digest"):
                reasons.append("library manifest differs from build evidence")
    # The native per-tap result schema retains executable and image identities;
    # binary_digest is checked when present, but is not emitted by that runtime.
    for key in (("executable_digest",) if library else ("image_digest", "executable_digest")):
        expected_artifact = artifacts.get(key)
        if not _immutable(target.get(key)) or (identity["kind"] == "candidate" and not _immutable(expected_artifact)) or (expected_artifact is not None and target.get(key) != expected_artifact):
            reasons.append(f"missing or mismatched {key}")
    if target.get("binary_digest") is not None and (not _immutable(target["binary_digest"]) or artifacts.get("binary_digest") is not None and target["binary_digest"] != artifacts["binary_digest"]):
        reasons.append("mismatched binary_digest")
    return reasons


def _control_errors(control: Any, *, scenario_id: str, nodes: list[dict], mode: str, manifest: dict, builds: dict, expected_targets: dict, expected_boundary: str | None = None, library=False, node_modes: dict[str, str] | None = None) -> list[str]:
    """Bind optional observations to the control's own run and expected scope.

    Controls may be separate runs. Legacy diagnostics can omit optional node and
    boundary metadata; supplied bindings and assertion results must agree.
    Security controls receive the same per-node modes as the security run.
    Measurement controls retain their independently selected exercise mode.
    """
    if control is None:
        return ["missing normal control"]
    control = _object(control, "normal_control")
    reasons = []
    expected_digest = manifest["definitions"]["scenarios"][scenario_id]["digest"]
    if control.get("scenario_id") != scenario_id or control.get("scenario_digest") != expected_digest:
        reasons.append("normal control scenario provenance differs")
    if control.get("run_id") is not None:
        _text(control["run_id"], "normal_control.run_id")
    if not control.get("run_id") or control.get("status") != "pass":
        reasons.append("normal control did not succeed")
    observations = _rows(control.get("evidence", []), "normal_control.evidence")
    assertions = _rows(control.get("assertions", []), "normal_control.assertions")
    verdict_records = [control, *observations, *assertions]
    records = list(verdict_records)
    for observation in observations + assertions:
        if isinstance(observation.get("data_points_used"), list):
            records.extend(_rows(observation["data_points_used"], "control.data_points_used"))
    node_ids = {node["node_id"] for node in nodes}
    # Only actual control/assertion records carry assertion verdicts. Native
    # points retain raw operation payloads whose result may itself be an object.
    # Points still participate in metadata binding, never in verdict selection.
    for record in verdict_records:
        if "result" in record:
            result = _text(record["result"], "normal_control.result")
            if result in {"fail", "skip", "error"}:
                reasons.append("normal control contains an explicit unsuccessful result")
            elif result != "pass":
                raise ValueError("normal_control.result must be pass, fail, skip or error")
    for record in records:
        for field in ("run_id", "node_id", "boundary"):
            present = field in record or any(field in _object(record[key], f"control.{key}")
                                            for key in ("fields", "payload", "correlation") if key in record)
            if not present:
                continue
            value = _text(_field(record, field), f"normal_control.{field}")
            expected = node_ids if field == "node_id" else {control.get("run_id")} if field == "run_id" else {expected_boundary}
            if value not in expected:
                reasons.append(f"normal control {field} differs from its retained run/entry/cell binding")
    has_evidence = False
    for observation in observations + assertions:
        if any(key in observation for key in ("primitive", "data_points_used", "evaluated_value")):
            adequate = observation.get("result") == "pass" and _assertion_nonvacuous(observation)
            if not adequate:
                reasons.append("normal control assertion has no successful non-vacuous evidence")
        else:
            adequate = isinstance(observation.get("diagnostic"), str) and bool(observation["diagnostic"].strip())
        has_evidence = has_evidence or adequate
    if not has_evidence:
        reasons.append("normal control has no non-vacuous retained evidence")
    targets = _object(control.get("targets", {}), "normal_control.targets")
    if set(targets) != {n["node_id"] for n in nodes}:
        reasons.append("normal control target node set differs")
    for node in nodes:
        target_mode = node_modes[node["node_id"]] if node_modes is not None else mode
        reasons.extend(_target_errors(targets.get(node["node_id"]), node, target_mode, manifest, builds, library=library))
        actual = targets.get(node["node_id"])
        expected = expected_targets.get(node["node_id"])
        actual = {} if actual is None else _object(actual, "normal_control.target")
        expected = {} if expected is None else _object(expected, "expected target")
        for field in ("image_digest", "executable_digest", "binary_digest"):
            if actual.get(field) != expected.get(field):
                reasons.append(f"normal control {field} differs from observed run target")
    return reasons


def _shared_control_input(control: dict, target_nodes: list[str], boundary: str | None = None) -> bool:
    """Require one positive input bound to every control target and its own run.

    Boundary omissions remain usable only when no boundary is available. When
    observations name boundaries, the same digest must reach the same boundary.
    Legacy and measurement control validation does not invoke this extra gate.
    """
    inputs = {node_id: set() for node_id in target_nodes}
    expected_boundary = control.get("boundary") or boundary
    if expected_boundary == "unobserved":
        expected_boundary = None
    for observation in _rows(control.get("evidence", []), "normal_control.evidence"):
        node_id = _field(observation, "node_id")
        digest = _field(observation, "input_digest")
        observed_boundary = _field(observation, "boundary")
        diagnostic = _field(observation, "diagnostic")
        kind = _field(observation, "kind")
        if (not isinstance(node_id, str) or node_id not in inputs
                or not control.get("run_id") or _field(observation, "run_id") != control["run_id"]
                or not _immutable(digest) or not isinstance(diagnostic, str) or not diagnostic.strip()
                or kind is not None and not isinstance(kind, str)
                or kind in ("http", "timeout", "endpoint-loss")
                or observed_boundary is not None and not isinstance(observed_boundary, str)
                or expected_boundary is not None and observed_boundary != expected_boundary):
            continue
        inputs[node_id].add((digest, observed_boundary))
    return bool(inputs and set.intersection(*inputs.values()))


def _observation_key(row: dict, run_id: str, boundary: str) -> tuple | None:
    for key in ("node_id", "run_id", "boundary", "input_digest", "diagnostic", "kind"):
        if key in row:
            _text(row[key], f"observation.{key}")
    if (row.get("run_id") != run_id or row.get("boundary") != boundary
            or not _immutable(row.get("input_digest")) or not row.get("diagnostic")
            or not row.get("node_id") or row.get("kind") in {"http", "timeout", "endpoint-loss"}):
        return None
    return (row.get("node_id"), row["input_digest"])


def _security(entry: dict, row: dict | None, manifest: dict, builds: dict, topology: dict) -> dict:
    output = {"entry_id": entry["id"], "classification": "inconclusive", "reasons": []}
    if row is None:
        output["reasons"] = ["missing security run evidence"]
        return output
    run_id = _text(row.get("run_id"), "security.run_id")
    output["run_id"] = run_id
    mode = row.get("mode")
    if not isinstance(mode, str) or mode not in _MODES:
        raise ValueError("security.mode must be stock, patched or coverage")
    invalid = []
    infrastructure = []
    for error in _rows(row.get("errors", []), "security.errors"):
        if error.get("kind") not in {"harness", "infrastructure"}:
            raise ValueError("security error kind must be harness or infrastructure")
        (invalid if error["kind"] == "harness" else infrastructure).append(_text(error.get("message"), "error.message"))
    contract = entry["contract"]
    scenario_id = contract["scenario_id"]
    definition = manifest["definitions"]["scenarios"][scenario_id]
    if row.get("scenario_id") != scenario_id or row.get("scenario_digest") != definition["digest"] or row.get("contract_digest") != _digest(contract):
        invalid.append("security scenario or contract provenance differs")
    nodes = topology["nodes"]
    # New plans freeze the actual mode for each participant. The row-wide mode
    # remains the authority only for historical nodes without a frozen mode.
    node_modes = {node["node_id"]: node["identity"].get("mode", mode) for node in nodes}
    targets = _object(row.get("targets", {}), "security.targets")
    if set(targets) != {n["node_id"] for n in nodes}:
        invalid.append("security target node set differs")
    for node in nodes:
        invalid.extend(_target_errors(targets.get(node["node_id"]), node, node_modes[node["node_id"]], manifest, builds, library=topology["mode"] == "library"))
    control_errors = _control_errors(row.get("normal_control"), scenario_id=contract["normal_control"]["scenario_id"],
                                     nodes=nodes, mode=mode, manifest=manifest, builds=builds, expected_targets=targets, expected_boundary=row.get("boundary"), library=topology["mode"] == "library", node_modes=node_modes)
    incomplete = []
    execution = entry.get("execution")
    if execution:
        if (len({node["identity"]["implementation"] for node in nodes}) > 1 and row.get("normal_control") is not None
                and not _shared_control_input(row["normal_control"], contract["target_nodes"], row.get("boundary"))):
            invalid.append("normal control lacks one shared input bound to every target in its retained run/boundary")
        execution_digest = _digest(execution)
        if execution.get("compatibility", {}).get("status") == "declared-compatible":
            try:
                _check_pair(nodes)
                if execution["compatibility"] != _pair_status(nodes):
                    invalid.append("execution compatibility differs from the frozen node prerequisites")
            except ValueError as exc:
                invalid.append(f"execution compatibility differs: {exc}")
        if row.get("execution_digest") != execution_digest:
            invalid.append("security execution digest differs from the frozen contract")
        if row.get("normal_control") is not None and row["normal_control"].get("execution_digest") != execution_digest:
            invalid.append("normal control execution digest differs from the frozen contract")
    if execution and execution.get("compatibility", {}).get("status") != "declared-compatible":
        incomplete.append("execution compatibility is not declared-compatible")
    if row.get("normal_control") is None:
        incomplete.extend(control_errors)
    else:
        invalid.extend(control_errors)
    boundary = row.get("boundary")
    if not isinstance(boundary, str) or not boundary.strip():
        incomplete.append("missing declared boundary")
    reached = set()
    for observation in _rows(row.get("reachability", []), "reachability"):
        activity = _count(observation.get("activity_count", 0), "reachability.activity_count")
        key = _observation_key(observation, run_id, boundary)
        if key and activity > 0:
            reached.add(key)
    if not set(contract["target_nodes"]) <= {key[0] for key in reached}:
        incomplete.append("intended workload/fault did not demonstrably reach every target boundary")
    oracle = _object(row.get("oracle", {}), "oracle")
    observations = _rows(oracle.get("observations", []), "oracle.observations")
    correlated = {_observation_key(obs, run_id, boundary) for obs in observations}
    target_inputs = [{key[1] for key in reached & correlated if key[0] == node_id}
                     for node_id in contract["target_nodes"]]
    if not set.intersection(*target_inputs):
        incomplete.append("missing one shared input with same-run, same-boundary reachability and oracle evidence for every target")
    if oracle.get("claim") == "node-crash" and not any(
        obs.get("kind") in {"process", "health"} and obs.get("process_exited") is True
        and _observation_key(obs, run_id, boundary) in reached for obs in observations
    ):
        incomplete.append("crash claim lacks independent process/health evidence; HTTP errors are insufficient")
    assertions = _rows(row.get("assertions", []), "assertions")
    # Match scenario._primitive_refs: declarations flatten primitive parameters
    # beside the structural primitive selector; retained rows nest them in params.
    declared = {(a["primitive"], serialize_campaign({key: value for key, value in a.items() if key != "primitive"}))
                for a in definition["document"].get("assertions", [])}
    observed = set()
    for assertion in assertions:
        primitive = _text(assertion.get("primitive"), "assertion.primitive")
        identity = (primitive, serialize_campaign(_object(assertion.get("params", {}), "assertion.params")))
        if identity in observed:
            raise ValueError(f"duplicate assertion identity: {primitive}")
        if identity not in declared:
            invalid.append(f"assertion {primitive} is not declared in the frozen scenario")
        observed.add(identity)
        if not isinstance(assertion.get("result"), str) or assertion["result"] not in {"pass", "fail", "skip", "error"}:
            raise ValueError("assertion.result must be pass, fail, skip or error")
        if assertion["result"] == "error":
            invalid.append(f"assertion {primitive} failed to evaluate")
        if assertion["result"] in {"skip", "error"} or not _assertion_nonvacuous(assertion):
            incomplete.append(f"assertion {primitive} has no decisive oracle points")
    if not declared or not declared <= observed:
        incomplete.append("missing declared security assertions")
    finding = any(a["result"] == "fail" for a in assertions)
    output["classification"] = ("harness-invalid" if invalid else "infrastructure-invalid" if infrastructure
                                 else "inconclusive" if incomplete else "finding" if finding else "pass")
    output["reasons"] = invalid + infrastructure + incomplete
    return output


def _field(row: dict, key: str) -> Any:
    values = [row[key]] if key in row else []
    for name in ("fields", "payload", "correlation"):
        if name in row:
            nested = _object(row[name], f"signal.{name}")
            if key in nested:
                values.append(nested[key])
    for value in values:
        if key != "value" and value is not None and (isinstance(value, (dict, list, bool))):
            raise ValueError(f"signal.{key} must be a scalar correlation or outcome value")
    return values[0] if values and all(value == values[0] for value in values) else None


def _event(row: dict) -> Any:
    return row.get("event", row.get("kind"))


def _has_sample(row: dict) -> bool:
    if _field(row, "value") is not None or isinstance(_event(row), str) and _event(row).strip():
        return True
    # Native instrumentation samples can carry a typed duration directly.
    return any(_field(row, field) is not None for field in
               ("duration_us", "elapsed_nanos", "decode_micros", "total_micros", "rss_bytes", "cpu_time_seconds",
                "disk_read_bytes", "disk_write_bytes", "network_rx_bytes", "network_tx_bytes", "open_fds", "threads"))


def _measurement(cell: dict, mode: str, row: dict | None, manifest: dict, builds: dict, node: dict) -> dict:
    output = {key: cell[key] for key in ("topology_id", "node_id", "measurement_id")}
    output.update(mode=mode, state="missing", reasons=[])
    # Invalid supplied evidence is an input error even when the runtime is
    # explicitly blocked. A blocker must not hide malformed retained records.
    if row is not None:
        result = _object(row.get("result"), "measurement.result")
        status = _text(result.get("status"), "measurement.result.status")
        if status not in _STATES:
            raise ValueError(f"unknown measurement result status: {status}")
        run_id = _text(result.get("run_id"), "measurement.result.run_id")
        samples = _rows(row.get("samples", []), "measurement.samples")
        events = _rows(row.get("events", []), "measurement.events")
        count = _count(result.get("sample_count", 0), "measurement.result.sample_count")
        for signal in samples + events:
            for field in ("run_id", "node_id", "target_node", "outcome"):
                _field(signal, field)
            if _event(signal) is not None:
                _text(_event(signal), "signal.event")
    if cell["runtime"]["status"] == "missing":
        output.update(state="blocked", reasons=[cell["runtime"]["reason"]])
        return output
    if row is None:
        fallback = cell.get("fallback", {})
        if fallback.get("status") == "blocked":
            output.update(state="blocked", reasons=[fallback["reason"]])
        else:
            output["reasons"] = ["missing measurement run evidence"]
        return output
    output["run_id"] = run_id
    reasons = []
    if row.get("candidate") != manifest["candidate"]:
        reasons.append("measurement candidate provenance differs")
    scenario_id = cell["scenario_id"]
    provenance = _object(result.get("provenance", {}), "measurement.result.provenance")
    fallback = cell.get("fallback", {})
    fallback_id = fallback.get("scenario_id")
    if (fallback.get("status") == "ready" and fallback_id in manifest["definitions"]["scenarios"]
            and provenance.get("scenario_id") == fallback_id):
        # Only the exact frozen, ready fallback can substitute for the primary
        # recipe. Its control and digest must bind to the same selected recipe.
        scenario_id = fallback_id
    if row.get("scenario_digest") != manifest["definitions"]["scenarios"][scenario_id]["digest"]:
        reasons.append("measurement scenario digest differs")
    if provenance.get("scenario_id") != scenario_id:
        reasons.append("measurement scenario identity differs")
    if result.get("measurement_id") != cell["measurement_id"] or _object(result.get("correlation", {}), "measurement.result.correlation").get("definition_digest") != cell["definition_digest"]:
        reasons.append("measurement tap definition provenance differs")
    library = next(topology["mode"] for topology in manifest["topologies"] if topology["id"] == cell["topology_id"]) == "library"
    reasons.extend(_target_errors(result.get("target"), node, mode, manifest, builds, library=library))
    reasons.extend(_control_errors(row.get("normal_control"), scenario_id=scenario_id, nodes=[node], mode=mode, manifest=manifest, builds=builds,
                                   expected_targets={node["node_id"]: result.get("target") or {}}, expected_boundary=row.get("boundary"), library=library))
    if mode == "patched" and provenance.get("experimental_consent") is not True:
        reasons.append("patched measurement requires retained experimental consent")
    rule = cell["rule"]
    minimum = rule.get("min_samples")
    if minimum is not None and (count < minimum or len(samples) < minimum or count > len(samples)):
        reasons.append(f"required {minimum} retained samples; reported={count}, retained={len(samples)}")
    if any(not _has_sample(sample) for sample in samples):
        reasons.append("correlation identifiers alone are not measurement samples")
    if not samples and not events:
        reasons.append("no retained sample or terminal event evidence")
    correlation = set(rule.get("correlation", [])) | {"run_id"}
    for signal in samples + events:
        if any(_field(signal, key) is None or _field(signal, key) == "" for key in correlation) or _field(signal, "run_id") != run_id:
            reasons.append("sample/event correlation is missing, ambiguous or belongs to another run")
            break
        if any(_field(signal, key) not in {None, cell["node_id"]} for key in ("node_id", "target_node")):
            reasons.append("sample/event belongs to another node")
            break
    if rule.get("required_event") and not any(_event(event) == rule["required_event"] for event in events):
        reasons.append("required terminal event is missing")
    if not set(rule.get("outcomes", [])) <= {_field(event, "outcome") for event in samples + events}:
        reasons.append("required accepted/rejected outcomes are missing")
    if rule.get("restart") and not any(_event(event) == "restart_started" for event in events):
        reasons.append("intentional restart evidence is missing")
    if rule.get("progress"):
        progress = _object(row.get("progress", {}), "measurement.progress")
        start, end = progress.get("start_block"), progress.get("end_block")
        if start is None or end is None or _count(end, "progress.end_block") - _count(start, "progress.start_block") < rule["progress"]["min_blocks"]:
            reasons.append("required chain progress is missing")
    if rule.get("coverage_envelope"):
        coverage = _object(result.get("coverage", {}), "measurement.result.coverage")
        envelope = rule["coverage_envelope"]
        paths = coverage.get("paths", [])
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError("coverage.paths must be an array of strings")
        if _count(coverage.get("covered_functions", 0), "coverage.covered_functions") < envelope["min_covered_functions"] or not set(envelope["required_paths"]) <= set(paths):
            reasons.append("required coverage envelope is missing")
    # An execution state is not a verification claim. Explicit unavailable,
    # failed, blocked, reserved and exercised states remain visible even when
    # their retained observations are insufficient.
    output["state"] = status if status in {"unavailable", "failed", "blocked", "reserved", "exercised"} else "incomplete" if reasons else "verified"
    output["reasons"] = reasons
    if status in {"unavailable", "failed", "blocked", "reserved"}:
        output["reasons"].append(result.get("unavailable_reason") or f"collector state is {status}")
    return output


def evaluate_campaign_evidence(manifest: dict[str, Any], evidence: dict[str, Any]) -> dict[str, dict]:
    """Return deterministic independent security-validity/completeness records.

    Per-entry invalidity precedes finding; an incomplete oracle is inconclusive.
    Campaign precedence is harness-invalid, infrastructure-invalid, finding,
    inconclusive, pass. Any finding remains visible among other inconclusive
    entries; each entry retains its own classification. Measurement completeness
    requires every frozen node/tap/mode cell to be verified.
    """
    _object(manifest, "manifest")
    _schema_validate(manifest)
    if manifest["campaign_digest"] != digest_security_campaign(manifest) or manifest["campaign_id"] != "campaign-" + manifest["campaign_digest"].split(":")[1]:
        raise ValueError("campaign manifest identity/digest mismatch")
    _validate_references(manifest)
    _object(evidence, "evidence")
    if evidence.get("schema_version") != "v1" or evidence.get("campaign_digest") != manifest["campaign_digest"]:
        raise ValueError("evidence schema or campaign digest mismatch")
    # Canonicalization also rejects NaN and non-JSON inputs, even in unused data.
    evidence_digest = _digest(evidence)
    builds = _index(_rows(evidence.get("builds", []), "builds"), ("mode",), "build")
    if any(key[0] not in _MODES for key in builds):
        raise ValueError("unknown build mode")
    security = _index(_rows(evidence.get("security", []), "security"), ("entry_id",), "security entry")
    if set(security) - {(entry["id"],) for entry in manifest["entries"]}:
        raise ValueError("unknown security entry")
    topologies = {topology["id"]: topology for topology in manifest["topologies"]}
    entries = [_security(entry, security.get((entry["id"],)), manifest, builds, topologies[entry["topology_id"]]) for entry in sorted(manifest["entries"], key=lambda e: e["id"])]
    fields = ("topology_id", "node_id", "measurement_id", "mode")
    measurements = _index(_rows(evidence.get("measurements", []), "measurements"), fields, "measurement cell")
    expected = {}
    for cell in manifest["required_tap_cells"]:
        for mode in cell["target_modes"]:
            key = tuple(cell[field] for field in fields[:-1]) + (mode,)
            if key in expected:
                raise ValueError(f"duplicate frozen measurement cell: {key}")
            expected[key] = cell
    if set(measurements) - set(expected):
        raise ValueError("unknown measurement cell")
    cells = []
    for key, cell in sorted(expected.items()):
        nodes = {node["node_id"]: node for node in topologies[cell["topology_id"]]["nodes"]}
        cells.append(_measurement(cell, key[-1], measurements.get(key), manifest, builds, nodes[cell["node_id"]]))
    common = {"schema_version": "v1", "campaign_id": manifest["campaign_id"],
              "campaign_digest": manifest["campaign_digest"], "evidence_digest": evidence_digest}
    classification = next((value for value in _PRECEDENCE if any(entry["classification"] == value for entry in entries)), "inconclusive")
    return {"security_validity": {**common, "kind": "campaign-security-validity", "classification": classification,
                                  "security_valid": bool(entries) and all(entry["classification"] in {"pass", "finding"} for entry in entries), "entries": entries},
            "measurement_completeness": {**common, "kind": "campaign-measurement-completeness",
                                         "measurement_complete": bool(cells) and all(cell["state"] == "verified" for cell in cells), "cells": cells}}


def _read_json(path: Path) -> dict:
    def pairs(items):
        document = {}
        for key, value in items:
            if key in document:
                raise ValueError(f"duplicate JSON key {key} in {path}")
            document[key] = value
        return document
    def invalid_constant(value):
        raise ValueError(f"non-finite JSON value {value} in {path}")
    try:
        return _object(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant), str(path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read campaign evidence {path}: {exc}") from exc


def persist_campaign_evaluation(manifest_path: Path, evidence_path: Path) -> dict[str, Path]:
    """Read two JSON files and atomically write separate derived sibling records.

    Inputs are never modified. Each output binds the original campaign digest
    and canonical evidence digest. Re-evaluation is deterministic; output paths
    cannot alias either input, and existing output symlinks are rejected.
    """
    manifest_path, evidence_path = Path(manifest_path), Path(evidence_path)
    manifest, evidence = _read_json(manifest_path), _read_json(evidence_path)
    evaluation = evaluate_campaign_evidence(manifest, evidence)
    paths = {key: manifest_path.parent / f"{manifest['campaign_id']}.{key.replace('_', '-')}.json" for key in evaluation}
    protected = {manifest_path.resolve(), evidence_path.resolve()}
    for path in paths.values():
        if path.is_symlink() or path.resolve() in protected:
            raise ValueError(f"derived evaluation path aliases retained evidence: {path}")
    for key, path in paths.items():
        atomic_write_bytes(path, (serialize_campaign(evaluation[key]) + "\n").encode("utf-8"))
    return paths
