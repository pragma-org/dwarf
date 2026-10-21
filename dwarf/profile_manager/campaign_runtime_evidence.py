"""Translate existing retained runtime facts into the campaign evidence index.

Supported native inputs: manifest.json, assertions.json, log.ndjson structured
CBOR outcomes, per-tap measurements/<id>/result.json, declared collector
samples.json/normalized.ndjson/result.json, matched exact-target proofs and
calibration result.json/attempts.ndjson positive controls with health checks.
No campaign-evidence sidecar or exit-status-only security verdict is consumed.
Missing input hashes, node identities, controls or predicates remain missing.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from profile_manager.run_ledger import atomic_write_bytes
from profile_manager.security_campaign import _digest

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# Campaign projection limits, independent of the native collector's cardinality.
# Reject oversized populations before normalization/join/output allocation; their
# original events and artifacts remain available as incomplete evidence.
TXSUBMISSION_MAX_PROJECTED_SAMPLES = 4096
TXSUBMISSION_MAX_SOURCE_EVENTS = 16384


def _path(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in str(relative):
        raise ValueError("native artifact path escapes retained run")
    result = Path(root) / path
    result.resolve().relative_to(Path(root).resolve())
    for parent in [result, *result.parents]:
        if parent == Path(root).parent:
            break
        if parent.is_symlink():
            raise ValueError("native artifact path contains a symlink")
    return result


def _json(root, relative, default=None):
    path = _path(root, relative)
    return json.loads(path.read_text()) if path.is_file() else default


def _ndjson(root, relative):
    path = _path(root, relative)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


def _refs(document, location=()):
    if isinstance(document, list):
        for i, row in enumerate(document):
            yield from _refs(row, (*location, i))
    elif isinstance(document, dict):
        if "target_id" in document and "manifests_dir" in document:
            yield location, document
        for key, value in document.items():
            yield from _refs(value, (*location, key))


def freeze_library_targets(manifest, scenario_id, topology, builds, mode, directory):
    """Reuse the native target loader, then freeze observed manifest/binary bytes.

    A registered target must identify the selected source commit and match the
    build lane's exact executable digest. Optional build.library_targets maps a
    target ID to its executable_digest and manifest_digest when the node binary
    and library harness differ. Otherwise artifacts.executable_digest is used.
    No target supplied by a candidate is loaded: refs come from the trusted
    scenario and native target catalog, checked by the campaign entry point.
    """
    from scripts.runtime_cardano_cbor_dataset_differential import _load_target
    document = manifest["definitions"]["scenarios"][scenario_id]["document"]
    refs = list(_refs(document))
    if not refs:
        raise ValueError("library scenario has no supported exact target-manifest binding")
    bindings = []
    covered = set()
    for location, ref in refs:
        target_id = ref["target_id"]
        manifests = Path(ref["manifests_dir"])
        if not manifests.is_absolute():
            manifests = Path(__file__).resolve().parents[2] / manifests
        target = _load_target(target_id, manifests)
        source = (manifests / f"{target_id}.yaml").read_bytes()
        source_digest = "sha256:" + hashlib.sha256(source).hexdigest()
        nodes = [node for node in topology["nodes"] if node["implementation"] == target["implementation"]]
        if len(nodes) != 1:
            raise ValueError("library manifest must bind one exact topology node")
        node = nodes[0]
        selected = node["identity"]
        if target["upstream_commit"] != selected["source_revision"]:
            raise ValueError("library source identity differs from the frozen candidate")
        build = next((row for row in builds if row["mode"] == mode), {}) if selected["kind"] == "candidate" else {}
        if selected["kind"] == "candidate" and build.get("source") != manifest["candidate"]:
            raise ValueError("library target requires a build of the frozen candidate")
        declaration = build.get("library_targets", {}).get(target_id, {})
        expected = declaration.get("executable_digest") or (build.get("artifacts") or selected["artifacts"]).get("executable_digest")
        executable_digest = "sha256:" + target["binary_sha256"]
        if not _DIGEST.fullmatch(str(expected)) or expected != executable_digest:
            raise ValueError("library executable digest differs from the selected artifact")
        if declaration.get("manifest_digest", source_digest) != source_digest:
            raise ValueError("library target manifest digest differs from build evidence")
        binary_bytes = Path(target["binary"]).read_bytes()
        if "sha256:" + hashlib.sha256(binary_bytes).hexdigest() != executable_digest:
            raise ValueError("library executable changed during resolution")
        root = Path(directory) / "library-inputs" / _digest([source_digest, executable_digest]).split(":")[1]
        root.mkdir(parents=True, exist_ok=True)
        binary = root / "executable"
        if binary.exists() and binary.read_bytes() != binary_bytes:
            raise ValueError("frozen library executable was changed")
        if not binary.exists():
            atomic_write_bytes(binary, binary_bytes)
            binary.chmod(0o500)
        frozen = json.loads(source)
        frozen["binary"] = str(binary.resolve())
        frozen_dir = root / "manifests"
        frozen_dir.mkdir(exist_ok=True)
        frozen_body = (json.dumps(frozen, sort_keys=True) + "\n").encode()
        destination = frozen_dir / f"{target_id}.yaml"
        if destination.exists() and destination.read_bytes() != frozen_body:
            raise ValueError("frozen library manifest was changed")
        if not destination.exists():
            atomic_write_bytes(destination, frozen_body)
            destination.chmod(0o400)
        identity = {"implementation": selected["implementation"], "version": selected["version"],
                    "source_revision": selected["source_revision"], "source_tree_digest": selected["source_tree_digest"],
                    "mode": mode, "runtime": "library", "target_id": target_id,
                    "library_manifest_digest": source_digest, "executable_digest": executable_digest,
                    "binary_digest": executable_digest}
        for key, field in (("patch_set_digest", "patch_set_sha256"), ("instrumentation_digest", "coverage_harness_sha256")):
            if build.get(key): identity[field] = build[key].removeprefix("sha256:")
        bindings.append({"location": list(location), "target_id": target_id, "node_id": node["node_id"],
                         "manifests_dir": str(frozen_dir.resolve()), "identity": identity,
                         "manifest": frozen, "source_manifest_digest": source_digest})
        covered.add(node["node_id"])
    if covered != {node["node_id"] for node in topology["nodes"]}:
        raise ValueError("library target bindings do not cover every selected node")
    return bindings


def _correlate(signal, run_id):
    signal = deepcopy(signal)
    if signal.get("run_id", run_id) != run_id:
        raise ValueError("native signal belongs to another run")
    # The signal's contained, declared artifact and canonical run directory
    # establish this run association. No input/node/outcome identifier is added.
    signal["run_id"] = run_id
    return signal


def _joined_queue_samples(rows, result, collector):
    """Project only the trusted merged-request join, retaining its full lineage."""
    from profile_manager.measurement_collectors import cardano_blockfetch_queue as queue
    from profile_manager.measurement_report import distribution_summary
    from profile_manager.campaign_evidence import _field

    correlations = ("run_id", "node_id", "target_node", "trace_session", "client_id", "request_id",
                    "block_hash", "header_hash", "tx_id", "input_id", "peer_id", "workload_phase",
                    "fault_id", "window", "outcome")
    # Native queue rows have an exact schema. Permit only explicit correlation
    # metadata alongside it, never reinterpret arbitrary numeric fields.
    metadata = set(correlations) - {"trace_session", "client_id", "request_id", "block_hash"}
    try:
        if not isinstance(result, dict) or result.get("status") != "collected":
            return []
        reported, records = result.get("sample_count"), result.get("records")
        if type(reported) is not int or reported <= 0 or not isinstance(records, list):
            return []
        if collector is not None:
            if not isinstance(collector, dict):
                return []
            export = collector.get("export", {})
            if not isinstance(export, dict) or export.get("incomplete", False) is not False:
                return []
        unique = {}
        for event in rows:
            if not isinstance(event, dict) or event.get("kind") != "blockfetch-handler-queue":
                return []
            native = queue.normalize({key: value for key, value in event.items()
                                      if key != "kind" and key not in metadata})
            if any(event.get(key) != value for key, value in native.items()):
                return []
            for key in metadata:
                value = _field(event, key)
                if isinstance(value, (int, float)) and (isinstance(value, bool) or not math.isfinite(value)):
                    return []
            unique[_digest(event)] = event
        events = [unique[key] for key in sorted(unique)]
        metrics, exercise, joined = queue.summarize(events, incomplete=False)
        if exercise["status"] != "pending-control-evidence":
            return []
        # Support the collector's four flat distributions only. Duplicate names,
        # partial shapes, counts or numeric summaries cannot select a population.
        reported_metrics = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("name"), str):
                return []
            name = record["name"]
            if name in reported_metrics or name not in metrics:
                return []
            expected = metrics[name]
            if set(record) != {"name", *expected}:
                return []
            for key, value in expected.items():
                actual = record[key]
                if isinstance(value, (int, float)):
                    if (isinstance(actual, bool) or not isinstance(actual, (int, float))
                            or not math.isfinite(actual) or actual != value
                            or isinstance(value, int) and type(actual) is not int):
                        return []
                elif actual != value:
                    return []
            reported_metrics[name] = record
        if set(reported_metrics) != set(metrics) or reported != max(m["sample_count"] for m in metrics.values()):
            return []
        metric = min(name for name, item in metrics.items()
                     if item["status"] == "available" and item["sample_count"] == reported)
        samples = []
        for request in joined["requests"]:
            contributors = [event for event in events if all(event.get(key) == request[key]
                            for key in ("trace_session", "client_id", "request_id"))]
            lineage = {"source_event_digests": sorted(_digest(event) for event in contributors)}
            for key in correlations:
                common = _field(contributors[0], key)
                if common is not None and common != "" and all(_field(event, key) == common for event in contributors):
                    lineage[key] = common
            if metric == "handler_service":
                service = sorted((e for e in contributors if e["phase"] == "service"), key=lambda e: e["started_monotonic_ns"])
                observations = [((end - start) / 1000, [_digest(event)])
                                for (start, end), event in zip(request["service_brackets_ns"], service)]
            elif metric == "pending_range_depth":
                pending = sorted((e for e in contributors if e["phase"] == "enqueue"), key=lambda e: e["update_index"])
                ack = next(e for e in contributors if e["phase"] == "dequeue")
                observations = [(e["queue_depth"], [_digest(e)]) for e in [*pending, ack]]
            else:
                # Keep the trusted interval endpoints distinct; no midpoint.
                observations = [(request[metric.replace("queue_", "") + "_ns"] / 1000,
                                 lineage["source_event_digests"])]
            samples.extend({"evidence_kind": "native-joined-metric", "metric": metric,
                            "value": value, "unit": metrics[metric]["unit"],
                            "value_source_event_digests": sources, **lineage} for value, sources in observations)
        if (len(samples) != reported or any(isinstance(s["value"], bool) or not math.isfinite(s["value"]) for s in samples)
                or distribution_summary(samples, unit=metrics[metric]["unit"]) != metrics[metric]):
            return []
        return samples
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return []


def _joined_txsubmission_samples(rows, result, collector):
    """Reconcile the complete native batch join before projecting one metric.

    Source lineage commits the complete sorted unique batch source digest list
    and its count. Value lineage identifies the phase snapshots or exact
    transaction endpoints. Handling is never an acceptance/adoption outcome.
    """
    from profile_manager.measurement_collectors import cardano_txsubmission_residence as residence
    from profile_manager.measurement_report import distribution_summary
    from profile_manager.campaign_evidence import _field

    correlations = ("run_id", "node_id", "target_node", "trace_session", "peer_id", "peer_session",
                    "request_id", "tx_id", "input_id", "workload_phase", "fault_id", "window", "outcome")
    metadata = set(correlations) - {"trace_session", "peer_id", "peer_session", "request_id", "tx_id", "outcome"}

    def exact(actual, expected):
        # Python equality alone accepts True == 1, including nested counts.
        if isinstance(expected, dict):
            return (isinstance(actual, dict) and set(actual) == set(expected)
                    and all(exact(actual[key], value) for key, value in expected.items()))
        if isinstance(expected, (int, float)):
            return (type(actual) in (int, float) and math.isfinite(actual) and actual == expected
                    and (type(expected) is not int or type(actual) is int))
        return type(actual) is type(expected) and actual == expected

    try:
        if len(rows) > TXSUBMISSION_MAX_SOURCE_EVENTS:
            return []
        if not isinstance(result, dict) or result.get("status") != "collected":
            return []
        reported, records = result.get("sample_count"), result.get("records")
        if (type(reported) is not int or not 0 < reported <= TXSUBMISSION_MAX_PROJECTED_SAMPLES
                or not isinstance(records, list)):
            return []
        if not isinstance(collector, dict) or not isinstance(collector.get("export"), dict):
            return []
        export = collector["export"]
        if (export.get("incomplete") is not False
                or not exact(export.get("rejected_record_count", 0), 0)
                or not exact(export.get("rejection_reasons", {}), {})
                or not exact(export.get("truncated_sources", []), [])):
            return []
        for event in rows:
            if not isinstance(event, dict) or event.get("kind") != residence.KIND:
                return []
            native = residence.normalize({key: value for key, value in event.items()
                                          if key != "kind" and key not in metadata})
            if not exact({key: event[key] for key in native}, native):
                return []
            for key in metadata:
                value = _field(event, key)
                if isinstance(value, (int, float)) and (isinstance(value, bool) or not math.isfinite(value)):
                    return []
        # Pass EVERY claimed row, including duplicates/conflicts, to the approved
        # summarizer. Never drop a malformed or incomplete batch to rescue others.
        metrics, exercise, joined = residence.summarize(rows, incomplete=False)
        if exercise["status"] != "pending-control-evidence":
            return []
        if not exact(collector.get("measurements"), metrics):
            return []
        reported_metrics = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("name"), str):
                return []
            name = record["name"]
            if name in reported_metrics or name not in metrics or not exact(record, {"name": name, **metrics[name]}):
                return []
            reported_metrics[name] = record
        if set(reported_metrics) != set(metrics) or reported != max(m["sample_count"] for m in metrics.values()):
            return []
        metric = min(name for name, item in metrics.items()
                     if item["status"] == "available" and item["sample_count"] == reported)
        batch_fields = ("trace_session", "peer_session", "peer_id", "request_id")
        batches, transactions = {}, {}
        for event in rows:
            key = tuple(event[field] for field in batch_fields)
            batches.setdefault(key, {})[_digest(event)] = event
        for tx in joined["transactions"]:
            transactions.setdefault(tuple(tx[field] for field in batch_fields), []).append(tx)
        samples = []
        for batch, unique in sorted(batches.items()):
            contributors = [unique[key] for key in sorted(unique)]
            lineage = {"source_event_set_digest": _digest(sorted(unique)), "source_event_count": len(unique)}
            for key in correlations:
                common = _field(contributors[0], key)
                if common is not None and common != "" and all(_field(e, key) == common for e in contributors):
                    lineage[key] = common
            observations = []
            if metric in {"pending_body_depth", "inflight_body_depth"}:
                field = metric.replace("_body", "")
                for phase in ("request", "body", "terminal"):
                    sources = [e for e in contributors if e["phase"] == phase]
                    observations.append((sources[0][field], sorted(_digest(e) for e in sources)))
            else:
                first_phase, last_phase = metric.split("_to_", 1)
                last_phase = last_phase.split("_", 1)[0]
                endpoints = {(e["tx_index"], e["phase"]): digest for digest, e in unique.items()}
                for tx in transactions[batch]:
                    sources = [endpoints[tx["tx_index"], phase] for phase in (first_phase, last_phase)]
                    observations.append((tx[metric + "_ns"] / 1000, sorted(sources)))
            samples.extend({"evidence_kind": "native-joined-metric", "metric": metric,
                            "value": value, "unit": metrics[metric]["unit"],
                            "value_source_event_digests": sources, **lineage} for value, sources in observations)
        if (len(samples) != reported or any(type(s["value"]) not in (int, float) or not math.isfinite(s["value"]) for s in samples)
                or not exact(distribution_summary(samples, unit=metrics[metric]["unit"]), metrics[metric])):
            return []
        return samples
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return []


def _normalized_signals(tap_id, rows, result, *, collector=None):
    """Use the collector's metric projection, not event presence, as evidence.

    A per-tap sample_count is the maximum count of one emitted metric. Select
    that exact metric deterministically and retain its contributing native rows;
    other normalized rows keep their event semantics. Duplicates and malformed
    values cannot supply an extra sample, and a reported count larger than the
    retained population remains incomplete in the campaign evaluator.
    """
    from profile_manager.measurement_collectors import amaru_stock, amaru_patched, cardano_stock, cardano_patched
    if tap_id == "cardano-patched-txsubmission-residence":
        # The shared producer stream mixes tap kinds. Preserve every claimed
        # row for strict whole-population validation and campaign event evidence.
        native = [row for row in rows if isinstance(row, dict) and
                  (row.get("kind") == "txsubmission-residence" or row.get("event") == "txsubmission_residence")]
        return _joined_txsubmission_samples(native, result, collector), native
    if tap_id == "cardano-patched-blockfetch-handler-queue":
        # The producer retains mixed tap kinds. This tap's signal population is
        # queue-only; keep every claimed queue row, including duplicates and
        # malformed kind/event mismatches, for strict validation and evidence.
        queue_rows = [row for row in rows if isinstance(row, dict)
                      and (row.get("kind") == "blockfetch-handler-queue"
                           or row.get("event") == "blockfetch_handler_queue")]
        return _joined_queue_samples(queue_rows, result, collector), queue_rows
    if tap_id.startswith("amaru-stock-"):
        project = lambda event: amaru_stock._build_measurements(tap_id, [event])[0]
    elif tap_id.startswith("amaru-patched-"):
        project = lambda event: amaru_patched._build_measurements(tap_id, [event])
    elif tap_id.startswith("cardano-stock-"):
        project = lambda event: cardano_stock._build_measurements(tap_id, [event])[0]
    elif tap_id.startswith("cardano-patched-"):
        project = lambda event: cardano_patched._measurements(tap_id, [event])
    else:
        project = lambda event: {}
    reported = result.get("sample_count", 0)
    names = sorted(record["name"] for record in result.get("records", [])
                   if isinstance(record.get("name"), str) and not isinstance(record.get("sample_count"), bool)
                   and record.get("sample_count") == reported and reported > 0
                   and record.get("status") in {"available", "collected"})
    metric = names[0] if names else None
    events, samples, seen = [], [], set()
    for event in rows:
        if (not isinstance(event, dict) or not isinstance(event.get("kind"), str)
                or "fields" in event and not isinstance(event["fields"], dict)):
            continue
        try:
            digest = _digest(event)
        except (ValueError, TypeError):
            continue
        if digest in seen:
            continue
        seen.add(digest)
        events.append(event)
        if metric is None:
            continue
        try:
            measured = project(event).get(metric, {})
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if measured.get("status") == "available" and measured.get("sample_count", 0) == 1:
            samples.append(event)
    if not samples and metric is None and reported in (0, None) and not isinstance(reported, bool) and tap_id.startswith("cardano-stock-"):
        # Cardano count-only metrics declare a scalar value rather than an
        # observation count. Reconcile that value against the actual native
        # producer projection of unique rows; the aggregate is ONE sample.
        counters = sorted((record for record in result.get("records", [])
                           if isinstance(record.get("name"), str) and "sample_count" not in record
                           and record.get("status") in {"available", "collected"}), key=lambda row: row["name"])
        for record in counters:
            value = record.get("value")
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                continue
            contributors = []
            for event in events:
                # A bare invented kind is insufficient: require the actual
                # stock normalizer's namespace/data contract and correlations.
                native = cardano_stock._normalize_record({"ns": event.get("namespace"), "data": event.get("fields"),
                    "at": event.get("timestamp"), "host": event.get("node"), "thread": event.get("thread")})
                if native is None or any(event.get(key) != item for key, item in native.items()):
                    continue
                try:
                    count = project(event).get(record["name"], {})
                except (KeyError, ValueError, TypeError, AttributeError):
                    continue
                if ("sample_count" not in count and count.get("status") == "available"
                        and count.get("unit") == record.get("unit") and count.get("value") == 1
                        and not isinstance(count.get("value"), bool)):
                    contributors.append(event)
            if not contributors or len(contributors) != value:
                continue
            aggregate = {"evidence_kind": "native-count-aggregate", "metric": record["name"],
                         "value": value, "unit": record.get("unit"),
                         "source_event_digests": sorted(_digest(event) for event in contributors)}
            # Keep only correlations shared by every contributing observation;
            # a multi-block aggregate cannot invent a single block identity.
            from profile_manager.campaign_evidence import _field
            for field in ("node_id", "target_node", "block_hash", "header_hash", "tx_id", "input_id", "peer_id",
                          "request_id", "workload_phase", "fault_id", "window", "outcome"):
                common = _field(contributors[0], field)
                if common is not None and all(_field(event, field) == common for event in contributors):
                    aggregate[field] = common
            samples = [aggregate]
            break
    return samples, events


def read_native_evidence(manifest, run_dir, *, scenario_id, topology, mode, plan, entry=None, cell=None, control=None):
    run_dir = Path(run_dir)
    run_id = run_dir.name
    native = _json(run_dir, "manifest.json", {})
    assertions = _json(run_dir, "assertions.json", [])
    events = _ndjson(run_dir, "log.ndjson")
    targets = {}
    bindings = (plan.get("scenario") or {}).get("library_target_bindings", [])
    for binding in bindings:
        targets[binding["node_id"]] = deepcopy(binding["identity"])
    proof = _json(run_dir, "outputs/client-example-proof/exact-target.json", {})
    if proof.get("matched") is True:
        target = proof.get("observed", {})
        matching = [node["node_id"] for node in topology["nodes"] if node["implementation"] == target.get("implementation")]
        if len(matching) == 1:
            targets[matching[0]] = deepcopy(target)
    decoder, positives = [], []
    decoder_incomplete = False
    for event in events:
        if event.get("primitive") != "cbor_fuzz_structured" or event.get("event") != "iteration":
            continue
        payload = event.get("payload", {})
        binding = next((row for row in bindings if row["target_id"] == payload.get("target_id")), None)
        digest = "sha256:" + str(payload.get("input_sha256", ""))
        if not binding or not _DIGEST.fullmatch(digest) or payload.get("timed_out") is not False or not isinstance(payload.get("returncode"), int):
            decoder_incomplete = True
            continue
        observation = {"run_id": run_id, "node_id": binding["node_id"], "boundary": "decoder", "input_digest": digest,
                       "diagnostic": str(payload.get("stdout_head") or f"decoder process exit {payload['returncode']}"),
                       "activity_count": 1, "kind": "process", "process_exited": True,
                       "outcome": payload.get("outcome"), "returncode": payload["returncode"]}
        decoder.append(observation)
        if payload.get("outcome") == "ok" and payload.get("mutated") is False and payload["returncode"] == 0:
            positives.append(observation)
    # Existing calibration legs retain positive wire controls and independent
    # process/progress observations. Their helper-local run_id names an output
    # subdirectory; containment in this canonical run binds the actual run ID.
    for implementation in ("amaru", "cardano"):
        relative = f"outputs/{implementation}-measurement-calibration"
        report = _json(run_dir, relative + "/result.json", {})
        target = report.get("target", {})
        matching = [node["node_id"] for node in topology["nodes"] if node["implementation"] == target.get("implementation")]
        if len(matching) != 1:
            continue
        node_id = matching[0]
        if node_id in targets and targets[node_id] != target:
            raise ValueError("conflicting native target identity observations")
        targets[node_id] = deepcopy(target)
        checks = (report.get("target_health") or {}).get("checks", {})
        required_checks = {"target_running_before", "target_running_after", "target_not_oom_killed",
                           "target_restart_count_unchanged", "honest_chain_progressed", "no_fatal_signals"}
        healthy = all(checks.get(key) is True for key in required_checks)
        for attempt in _ndjson(run_dir, relative + "/attempts.ndjson"):
            try:
                request = bytes.fromhex(attempt.get("request_hex", ""))
                response = bytes.fromhex(attempt.get("response_hex", ""))
            except (TypeError, ValueError):
                continue
            digest = "sha256:" + hashlib.sha256(request).hexdigest()
            if (healthy and request and response and digest == attempt.get("request_sha256")
                    and attempt.get("listener_reached") is True and attempt.get("outcome") == "accepted"
                    and attempt.get("expected_external_outcome") == "accepted" and attempt.get("detail") == "accept-response"):
                positives.append({"run_id": run_id, "node_id": node_id, "input_digest": digest,
                                  "diagnostic": "retained positive Handshake acceptance with independent live process and chain progress",
                                  "attempt_id": attempt.get("attempt_id"), "outcome": "accepted"})
    control_nodes = (
        {cell["node_id"]}
        if cell is not None
        else {node["node_id"] for node in topology["nodes"]}
    )
    control_targets = {
        node_id: target for node_id, target in targets.items() if node_id in control_nodes
    }
    control_record = {"run_id": run_id, "scenario_id": scenario_id,
                      "scenario_digest": manifest["definitions"]["scenarios"][scenario_id]["digest"],
                      "targets": control_targets, "status": "pass" if positives and set(control_targets) == control_nodes
                      and {row["node_id"] for row in positives if row["node_id"] in control_nodes} == control_nodes else "unknown",
                      "evidence": [row for row in positives if row["node_id"] in control_nodes], "assertions": []}
    execution_bound = bool(plan.get("campaign_execution_digest")) and (entry is None or "execution" in entry)
    requires_shared_control = execution_bound or (entry is not None and "execution" in entry)
    if requires_shared_control and len({node["identity"]["implementation"] for node in topology["nodes"]}) > 1:
        from profile_manager.campaign_evidence import _shared_control_input
        if not _shared_control_input(control_record, [node["node_id"] for node in topology["nodes"]]):
            control_record["status"] = "unknown"
    if execution_bound:
        control_record["execution_digest"] = plan["campaign_execution_digest"]
    result = {"run_id": run_id, "execution_complete": native.get("exit_status") in {"pass", "fail"},
              "native_assertions": assertions, "control": control_record, "measurements": []}
    if entry:
        result["security"] = {"entry_id": entry["id"], "run_id": run_id, "mode": mode, "scenario_id": scenario_id,
            "scenario_digest": manifest["definitions"]["scenarios"][scenario_id]["digest"],
            "contract_digest": _digest(entry["contract"]), "targets": targets,
            "normal_control": (control or {}).get("control") if (control or {}).get("control", {}).get("status") == "pass" else None,
            "assertions": assertions, "boundary": "decoder" if bindings else "unobserved", "reachability": decoder,
            "oracle": {"observations": [] if decoder_incomplete else decoder}, "errors": []}
        if "execution" in entry:
            result["security"]["execution_digest"] = plan.get("campaign_execution_digest")
    for required in manifest["required_tap_cells"]:
        if required["topology_id"] != topology["id"] or mode not in required["target_modes"]:
            continue
        tap_id = required["measurement_id"]
        measured = _json(run_dir, f"measurements/{tap_id}/result.json")
        if measured is None:
            continue
        if measured.get("run_id") != run_id or measured.get("measurement_id") != tap_id:
            raise ValueError("native per-tap result belongs to another run/tap")
        samples, normalized = [], []
        collector = {}
        for artifact in measured.get("artifacts", []):
            path = _path(run_dir, artifact)
            if artifact == f"measurements/collectors/{tap_id}/samples.json":
                samples = _json(run_dir, artifact, [])
            elif artifact == f"measurements/collectors/{tap_id}/normalized.ndjson":
                normalized = _ndjson(run_dir, artifact)
            elif artifact == f"measurements/collectors/{tap_id}/result.json":
                collector = _json(run_dir, artifact, {})
        normalized_samples, normalized = _normalized_signals(tap_id, normalized, measured, collector=collector)
        # Residence values come exclusively from the reconciled native join.
        # An arbitrary samples.json can never substitute or override its values.
        if tap_id == "cardano-patched-txsubmission-residence":
            samples = []
        # Explicit raw samples take precedence for other taps when both native
        # forms exist; normalized rows never double their sample population.
        if not samples:
            samples = normalized_samples
            if len(samples) == 1 and samples[0].get("evidence_kind") == "native-count-aggregate":
                # Derive the retained population without rewriting the native
                # artifact or treating the aggregate's numeric value as N rows.
                measured = deepcopy(measured)
                measured["provenance"] = {**measured.get("provenance", {}),
                    "campaign_sample_count_basis": {"kind": "one-retained-count-aggregate",
                        "native_sample_count": measured.get("sample_count"), "metric": samples[0]["metric"]}}
                measured["sample_count"] = 1
        # Bind only a reported node, or a unique implementation in this topology.
        reported = (collector.get("target") or {}).get("node")
        signals = samples + normalized
        ids = {row.get("target_node", row.get("node_id")) for row in signals} - {None}
        if tap_id == "cardano-patched-txsubmission-residence":
            ids = {row.get(key) for row in signals for key in ("node_id", "target_node")} - {None}
        if reported: ids.add(reported)
        if not ids:
            ids = {node["node_id"] for node in topology["nodes"] if node["implementation"] == measured.get("target", {}).get("implementation")}
        if ids != {required["node_id"]}:
            continue
        exact_control = deepcopy((control or {}).get("control"))
        if exact_control:
            exact_control["targets"] = {key: value for key, value in exact_control.get("targets", {}).items() if key == required["node_id"]}
        result["measurements"].append({"topology_id": topology["id"], "node_id": required["node_id"], "measurement_id": tap_id,
            "mode": mode, "candidate": deepcopy(manifest["candidate"]),
            "scenario_digest": manifest["definitions"]["scenarios"][scenario_id]["digest"],
            "normal_control": exact_control, "result": measured,
            "samples": [_correlate(row, run_id) for row in samples], "events": [_correlate(row, run_id) for row in normalized]})
    return result
