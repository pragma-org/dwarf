#!/usr/bin/env python3
"""Qualify and publish the exact Cardano measurement target from real runtime evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from profile_manager.measurement_collectors.cardano_patched import (
    CARDANO_MEASUREMENT_PATCH_SHA256,
    CARDANO_SOURCE_REVISION,
    load_cardano_patched_telemetry,
)
from scripts.build_cardano_measurement_target import (
    _artifact,
    publish_target_record,
    sha256_file,
)


class QualificationError(RuntimeError):
    pass


def _json(path: Path) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"cannot read evidence {path}: {error}") from error
    if not isinstance(body, dict):
        raise QualificationError(f"evidence is not an object: {path}")
    return body


def qualify_target(
    *, build_result_path: Path, runtime_root: Path, compose_report_path: Path,
    workload_result_path: Path, qualification_path: Path, registry_root: Path,
) -> dict[str, Any]:
    build = _json(build_result_path)
    runtime = _json(runtime_root / "runtime.json")
    compose = _json(compose_report_path)
    workload = _json(workload_result_path)
    if build.get("patch_set_sha256") != CARDANO_MEASUREMENT_PATCH_SHA256:
        raise QualificationError("build patch-set identity does not match")
    if (build.get("source") or {}).get("revision") != CARDANO_SOURCE_REVISION:
        raise QualificationError("build source revision does not match")
    image = build.get("image") or {}
    immutable = next(
        (str(value) for value in image.get("repo_digests") or [] if "@sha256:" in str(value)),
        None,
    )
    if immutable is None:
        raise QualificationError("build has no immutable image digest")
    image_digest = immutable.rsplit("@", 1)[-1]
    nodes = [node for node in runtime.get("nodes") or [] if node.get("impl") == "cardano-node"]
    if len(nodes) < 1:
        raise QualificationError("runtime has no Cardano-node target")
    node = nodes[0]
    exact_runtime = (
        node.get("target_mode") == "patched"
        and node.get("source_revision") == CARDANO_SOURCE_REVISION
        and node.get("patch_set_sha256") == CARDANO_MEASUREMENT_PATCH_SHA256
        and node.get("image_digest") == image_digest
    )
    if not exact_runtime:
        raise QualificationError("runtime target identity does not match the exact build")
    if compose.get("healthy") is not True:
        raise QualificationError("runtime topology is not healthy")
    if (compose.get("cardano_chain_progress_gate") or {}).get("ready") is not True:
        raise QualificationError("runtime topology did not prove chain progress")
    target = workload.get("target") or {}
    if (
        target.get("mode") != "patched"
        or target.get("source_revision") != CARDANO_SOURCE_REVISION
        or target.get("patch_set_sha256") != CARDANO_MEASUREMENT_PATCH_SHA256
        or target.get("image_digest") != image_digest
    ):
        raise QualificationError("workload target identity does not match the exact build")
    if int((workload.get("attempts") or {}).get("total") or 0) < 30:
        raise QualificationError("runtime workload has fewer than 30 protocol attempts")
    plutus = workload.get("plutus_workload") or {}
    plutus_outcomes = {row.get("outcome") for row in plutus.get("records") or []}
    if (
        int(plutus.get("transaction_count") or 0) < 2
        or not {"accepted", "rejected"}.issubset(plutus_outcomes)
    ):
        raise QualificationError(
            "runtime workload did not execute accepted and rejected Plutus outcomes"
        )
    traces = (
        runtime_root / "logs" / str(node.get("id")) / "cardano-measurement.ndjson",
        runtime_root / "logs" / str(node.get("id")) / "cardano-plutus.ndjson",
    )
    telemetry = load_cardano_patched_telemetry(traces)
    events = telemetry["events"]
    counts = {
        "protocol_receive_decode": sum(event["kind"] == "protocol-receive-decode" for event in events),
        "block_application": sum(event.get("stage") == "block-application" for event in events),
        "epoch_transition": sum(event.get("stage") == "epoch-transition" for event in events),
        "plutus_vm": sum(event.get("stage") == "plutus-vm" for event in events),
    }
    missing = sorted(name for name, count in counts.items() if count < 1)
    if missing:
        raise QualificationError(
            "runtime patched measurement samples are vacuous: " + ", ".join(missing)
        )
    measured_plutus_outcomes = {
        event.get("outcome")
        for event in events
        if event.get("stage") == "plutus-vm"
    }
    if not {"accepted", "rejected"}.issubset(measured_plutus_outcomes):
        raise QualificationError(
            "runtime Plutus timing does not contain accepted and rejected outcomes"
        )
    result = {
        "schema_version": 1,
        "status": "passed",
        "implementation": "cardano-node",
        "version": "11.1.2",
        "source_revision": CARDANO_SOURCE_REVISION,
        "patch_set_sha256": CARDANO_MEASUREMENT_PATCH_SHA256,
        "image_digest": image_digest,
        "fresh_runtime": runtime_root.name,
        "chain_progress": compose["cardano_chain_progress_gate"],
        "protocol_attempt_count": int(workload["attempts"]["total"]),
        "plutus_transaction_count": int(plutus["transaction_count"]),
        "sample_counts": dict(sorted(counts.items())),
        "plutus_measured_outcomes": sorted(measured_plutus_outcomes),
        "trace_artifacts": [
            {"name": path.name, "sha256": "sha256:" + sha256_file(path), "size_bytes": path.stat().st_size}
            for path in traces
        ],
    }
    qualification_path.parent.mkdir(parents=True, exist_ok=True)
    qualification_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    build_root = build_result_path.parent.parent
    build["build_result_sha256"] = sha256_file(build_result_path)
    build.setdefault("image", {})["runtime_probe"] = {
        "status": "passed",
        "log": _artifact(qualification_path, root=build_root),
    }
    record = publish_target_record(build, registry_root=registry_root)
    return {**result, "target_registry": record}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-result", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--compose-report", type=Path, required=True)
    parser.add_argument("--workload-result", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--target-registry", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = qualify_target(
            build_result_path=args.build_result.resolve(),
            runtime_root=args.runtime_root.resolve(),
            compose_report_path=args.compose_report.resolve(),
            workload_result_path=args.workload_result.resolve(),
            qualification_path=args.qualification.resolve(),
            registry_root=args.target_registry.resolve(),
        )
    except QualificationError as error:
        print(f"error: {error}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
