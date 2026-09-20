"""Run the frozen Card 01 corpus through one exact production codec adapter."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from scripts.runtime_cardano_cbor_dataset_differential import (
    QUALIFIED_DATASET_REPOSITORY,
    QUALIFIED_DATASET_REVISION,
    _dataset_digest,
    _git_revision,
    _select_inputs,
)


class ConformanceContractError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_adapter_record(
    path: Path, *, implementation: str, source_revision: str
) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceContractError(f"cannot read adapter record: {path}") from exc
    expected = {
        "schema_version": 1,
        "kind": "production-cbor-conformance",
        "implementation": implementation,
        "source_revision": source_revision,
        "measurement_boundary": "production-codec-only",
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise ConformanceContractError(
                f"adapter {key} mismatch: expected {value}, got {record.get(key)}"
            )
    executable = Path(os.path.expanduser(os.path.expandvars(str(record.get("executable") or ""))))
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ConformanceContractError(f"adapter executable is unavailable: {executable}")
    digest = str(record.get("executable_sha256") or "")
    if len(digest) != 64 or _sha256(executable) != digest:
        raise ConformanceContractError("adapter executable digest mismatch")
    build_digest = str(record.get("build_result_sha256") or "")
    if len(build_digest) != 64:
        raise ConformanceContractError("adapter build-result digest is missing")
    return {**record, "executable": str(executable.resolve())}


def _run_adapter(executable: str, payload: bytes, timeout_seconds: float) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [executable],
            input=payload,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConformanceContractError("production codec adapter timed out") from exc
    if process.returncode not in (0, 1):
        raise ConformanceContractError(
            f"production codec adapter exited with {process.returncode}"
        )
    try:
        result = json.loads(process.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConformanceContractError("production codec adapter returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ConformanceContractError("production codec adapter result must be an object")
    expected_exit = {"accepted": 0, "rejected": 1}.get(result.get("outcome"))
    if expected_exit is None or process.returncode != expected_exit:
        raise ConformanceContractError(
            "production codec adapter exit code does not match its terminal outcome"
        )
    return result


def _validate_result(
    result: dict[str, Any],
    *,
    implementation: str,
    source_revision: str,
    expected_outcome: str,
) -> dict[str, Any]:
    if result.get("schema_version") != "v1":
        raise ConformanceContractError("adapter result schema_version mismatch")
    if result.get("implementation") != implementation:
        raise ConformanceContractError("adapter result implementation mismatch")
    if result.get("source_revision") != source_revision:
        raise ConformanceContractError("adapter result source revision mismatch")
    if result.get("boundary") != "production-codec-only":
        raise ConformanceContractError("adapter result boundary mismatch")
    outcome = result.get("outcome")
    if outcome not in {"accepted", "rejected"}:
        raise ConformanceContractError("adapter result has no terminal outcome")
    elapsed_nanos = result.get("elapsed_nanos")
    if (
        isinstance(elapsed_nanos, bool)
        or not isinstance(elapsed_nanos, int)
        or elapsed_nanos < 0
    ):
        raise ConformanceContractError("adapter result has invalid elapsed_nanos")
    elapsed_micros = result.get("elapsed_micros")
    if (
        isinstance(elapsed_micros, bool)
        or not isinstance(elapsed_micros, int)
        or elapsed_micros != elapsed_nanos // 1_000
    ):
        raise ConformanceContractError(
            "adapter result elapsed_micros is not the integer nanosecond projection"
        )
    first = result.get("first_encode_hex")
    second = result.get("second_encode_hex")
    roundtrip = (
        outcome == "rejected"
        or (
            isinstance(first, str)
            and bool(first)
            and first == second
            and result.get("second_decode_outcome") == "accepted"
        )
    )
    return {
        **result,
        "expected_outcome": expected_outcome,
        "outcome_matches": outcome == expected_outcome,
        "roundtrip_consistent": roundtrip,
        "duration_micros": elapsed_nanos / 1_000,
    }


def _percentile(values: list[int], quantile: float) -> float:
    if not values:
        raise ConformanceContractError("cannot summarize an empty timing distribution")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * quantile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    nanos = [int(record["elapsed_nanos"]) for record in records]
    return {
        "sample_count": len(nanos),
        "minimum_micros": min(nanos) / 1_000,
        "p50_micros": _percentile(nanos, 0.50) / 1_000,
        "p95_micros": _percentile(nanos, 0.95) / 1_000,
        "maximum_micros": max(nanos) / 1_000,
        "unit": "us",
        "raw_unit": "ns",
    }


def _write_summary(path: Path, report: dict[str, Any]) -> None:
    checks = report["checks"]
    lines = [
        "# CBOR conformance result",
        "",
        "The adapter used the exact production codec boundary for the selected source revision.",
        "",
        "Child explanation: The test gave the node 100 messages. It checked whether the node accepted or rejected each message as expected.",
        "",
        "| Outcome | Samples | p50 | p95 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for outcome in ("accepted", "rejected"):
        distribution = report["distributions"].get(outcome)
        if distribution:
            lines.append(
                f"| {outcome} | {distribution['sample_count']} | "
                f"{distribution['p50_micros']:.3f} us | {distribution['p95_micros']:.3f} us |"
            )
    lines.extend(
        [
            "",
            f"Expected outcomes: {'PASS' if checks['cbor_conformance_clean'] else 'FAIL'}",
            f"Stable second encoding: {'PASS' if checks['cbor_roundtrip_consistent'] else 'FAIL'}",
            "",
            "Raw nanoseconds are retained in `inputs.ndjson`. Fractional microseconds are derived without removing the sub-microsecond part.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_cbor_conformance(
    config: dict[str, Any],
    *,
    adapter_runner: Callable[[str, bytes, float], dict[str, Any]] = _run_adapter,
) -> Path:
    repository = str(config.get("source_repository") or "")
    revision = str(config.get("dataset_revision") or "")
    if repository != QUALIFIED_DATASET_REPOSITORY:
        raise ConformanceContractError("CBOR dataset repository is not qualified")
    if revision != QUALIFIED_DATASET_REVISION:
        raise ConformanceContractError("CBOR dataset revision is not qualified")
    repo_dir = Path(str(config["dataset_repo_dir"])).resolve()
    if _git_revision(repo_dir) != revision:
        raise ConformanceContractError("CBOR dataset checkout revision mismatch")
    dataset_dir = Path(str(config["dataset_dir"])).resolve()
    selected = _select_inputs(dataset_dir, "plutus_data", 25)
    if len(selected) != 100:
        raise ConformanceContractError("frozen CBOR selection must contain 100 inputs")

    implementation = str(config["implementation"])
    source_revision = str(config["source_revision"])
    adapter = load_adapter_record(
        Path(str(config["adapter_record"])),
        implementation=implementation,
        source_revision=source_revision,
    )
    timeout = float(config.get("per_input_timeout_seconds", 5))
    output_dir = Path(str(config["output_dir"])).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    transcript = []
    for item in selected:
        expected = "accepted" if item["category"] == "valid" else "rejected"
        raw = adapter_runner(adapter["executable"], item["data"], timeout)
        result = _validate_result(
            raw,
            implementation=implementation,
            source_revision=source_revision,
            expected_outcome=expected,
        )
        transcript.append(
            {
                "input_id": item["relative_path"],
                "input_sha256": item["sha256"],
                "category": item["category"],
                **result,
            }
        )
    mismatches = [row["input_id"] for row in transcript if not row["outcome_matches"]]
    roundtrip_failures = [
        row["input_id"] for row in transcript if not row["roundtrip_consistent"]
    ]
    report = {
        "schema_version": "v1",
        "workload_identity": "conway-plutus-data-100-v1",
        "source": {
            "repository": repository,
            "revision": revision,
            "selection_sha256": _dataset_digest(selected),
        },
        "target": {
            "implementation": implementation,
            "source_revision": source_revision,
        },
        "adapter": {
            key: adapter[key]
            for key in (
                "kind",
                "implementation",
                "source_revision",
                "measurement_boundary",
                "executable_sha256",
                "build_result_sha256",
            )
        },
        "input_count": len(transcript),
        "records": transcript,
        "outcome_mismatches": mismatches,
        "roundtrip_failures": roundtrip_failures,
        "distributions": {
            outcome: _distribution([row for row in transcript if row["outcome"] == outcome])
            for outcome in ("accepted", "rejected")
            if any(row["outcome"] == outcome for row in transcript)
        },
        "checks": {
            "cbor_conformance_clean": not mismatches,
            "cbor_roundtrip_consistent": not roundtrip_failures,
        },
    }
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    with (output_dir / "inputs.ndjson").open("w", encoding="utf-8") as stream:
        for row in transcript:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    _write_summary(output_dir / "report.md", report)
    return report_path



def main(argv=None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ConformanceContractError("configuration must be an object")
        report = run_cbor_conformance(config)
    except (ConformanceContractError, KeyError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
