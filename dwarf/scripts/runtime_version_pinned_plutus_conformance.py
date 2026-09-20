#!/usr/bin/env python3
"""Run exact-revision Plutus V2 evaluators against one retained cost model."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
QUALIFIED_COST_MODEL = ROOT / "corpora" / "cardano-measurement" / "plutus-v2-cost-model-protocol-v10.json"
QUALIFIED_COST_MODEL_SHA256 = "675a27a3c1f2f9b32954c67c1f0ad21479713eef5513386638d78e05f5e277cc"
QUALIFIED_SCRIPTS = {
    "always-succeeds-v2.plutus": {
        "path": ROOT / "corpora" / "cardano-measurement" / "always-succeeds-v2.plutus",
        "sha256": "8ec6e6882130b15c84f6062125fc6d7665e1f72f8aa4e559b93d963b47c83a0a",
        "expected_outcome": "accepted",
    },
    "always-fails-v2.plutus": {
        "path": ROOT / "corpora" / "cardano-measurement" / "always-fails-v2.plutus",
        "sha256": "99f9d7bf49fdd9c257321942bedf074d562c5e6d50eb33bd134b53afc877ae34",
        "expected_outcome": "rejected",
    },
}


class PlutusContractError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_adapter_record(path: Path, *, implementation: str, source_revision: str) -> dict[str, Any]:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = {
        "kind": "production-plutus-v2-conformance",
        "implementation": implementation,
        "source_revision": source_revision,
        "measurement_boundary": "production-plutus-v2-vm-only",
    }
    for key, value in expected.items():
        if body.get(key) != value:
            raise PlutusContractError(f"adapter record has wrong {key}")
    executable = Path(str(body.get("executable") or ""))
    if not executable.is_file() or not executable.stat().st_mode & 0o111:
        raise PlutusContractError("adapter executable is unavailable")
    if _sha256(executable) != body.get("executable_sha256"):
        raise PlutusContractError("adapter executable digest mismatch")
    for key in ("executable_sha256", "build_result_sha256"):
        if not isinstance(body.get(key), str) or len(body[key]) != 64:
            raise PlutusContractError(f"adapter record has no valid {key}")
    return body


def _validate_result(
    body: Mapping[str, Any], *, implementation: str, source_revision: str,
    expected_outcome: str, cost_model_sha256: str,
) -> dict[str, Any]:
    expected = {
        "schema_version": "v1",
        "implementation": implementation,
        "source_revision": source_revision,
        "boundary": "production-plutus-v2-vm-only",
        "plutus_version": "v2",
        "cost_model_sha256": cost_model_sha256,
    }
    for key, value in expected.items():
        if body.get(key) != value:
            raise PlutusContractError(f"adapter result has wrong {key}")
    if body.get("outcome") not in {"accepted", "rejected"}:
        raise PlutusContractError("adapter result has invalid outcome")
    for key in ("cpu_budget", "memory_budget", "elapsed_nanos", "elapsed_micros"):
        value = body.get(key)
        if not isinstance(value, int) or value < 0:
            raise PlutusContractError(f"adapter result has invalid {key}")
    nanos = body["elapsed_nanos"]
    if body["elapsed_micros"] != nanos // 1000:
        raise PlutusContractError("adapter result elapsed_micros is inconsistent with elapsed_nanos")
    result = dict(body)
    result["expected_outcome"] = expected_outcome
    result["duration_micros"] = nanos / 1000
    return result


def _run_adapter(executable: str, request: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    completed = subprocess.run(
        [executable], input=(json.dumps(dict(request), sort_keys=True) + "\n").encode(),
        capture_output=True, timeout=timeout, check=False,
    )
    try:
        body = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlutusContractError("adapter did not return one JSON result") from exc
    wanted_exit = 0 if body.get("outcome") == "accepted" else 1
    if completed.returncode != wanted_exit:
        raise PlutusContractError("adapter exit code does not match outcome")
    return body


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[rank]


def _distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["duration_micros"]) for row in records]
    return {
        "sample_count": len(values),
        "min_micros": min(values),
        "p50_micros": _percentile(values, 0.50),
        "p95_micros": _percentile(values, 0.95),
        "max_micros": max(values),
    }


def run_plutus_conformance(
    config: Mapping[str, Any], *,
    adapter_runner: Callable[[str, Mapping[str, Any], float], dict[str, Any]] = _run_adapter,
) -> Path:
    output = Path(config["output_dir"])
    output.mkdir(parents=True, exist_ok=False)
    model = Path(config.get("cost_model") or QUALIFIED_COST_MODEL)
    if _sha256(model) != QUALIFIED_COST_MODEL_SHA256:
        raise PlutusContractError("cost model digest does not match the qualified input")
    costs = json.loads(model.read_text(encoding="utf-8"))
    if not isinstance(costs, list) or not costs or not all(isinstance(x, int) for x in costs):
        raise PlutusContractError("cost model must be a non-empty ordered integer array")
    count = int(config.get("executions_per_script", 30))
    if count < 1:
        raise PlutusContractError("executions_per_script must be positive")

    adapters = {}
    for implementation in ("amaru", "cardano-node"):
        item = config["adapters"][implementation]
        adapters[implementation] = load_adapter_record(
            Path(item["record"]), implementation=implementation,
            source_revision=str(item["source_revision"]),
        )

    records: list[dict[str, Any]] = []
    for script_name, item in QUALIFIED_SCRIPTS.items():
        script_path = Path(item["path"])
        if _sha256(script_path) != item["sha256"]:
            raise PlutusContractError(f"script digest mismatch: {script_name}")
        expected_outcome = item.get(
            "expected_outcome", "accepted" if "succeeds" in script_name else "rejected"
        )
        for execution in range(count):
            request = {
                "schema_version": "v1",
                "plutus_version": "v2",
                "protocol_version": 10,
                "script_name": script_name,
                "script_cbor_hex": json.loads(script_path.read_text(encoding="utf-8"))["cborHex"]
                    if script_path.suffix == ".plutus" and script_path.read_text(encoding="utf-8").lstrip().startswith("{")
                    else script_path.read_bytes().hex(),
                "script_sha256": item["sha256"],
                "cost_model": costs,
                "cost_model_sha256": QUALIFIED_COST_MODEL_SHA256,
                "arguments": [{"constructor": 0, "fields": []}] * 3,
                "expected_outcome": expected_outcome,
                "execution": execution,
            }
            for implementation, adapter in adapters.items():
                raw = adapter_runner(str(adapter["executable"]), request, 30.0)
                row = _validate_result(
                    raw, implementation=implementation,
                    source_revision=adapter["source_revision"],
                    expected_outcome=expected_outcome,
                    cost_model_sha256=QUALIFIED_COST_MODEL_SHA256,
                )
                row.update({"script_name": script_name, "script_sha256": item["sha256"], "execution": execution})
                records.append(row)

    mismatches = []
    grouped = {}
    for row in records:
        grouped.setdefault((row["script_name"], row["execution"]), {})[row["implementation"]] = row
    for (script_name, execution), pair in grouped.items():
        amaru = pair["amaru"]
        cardano = pair["cardano-node"]
        fields = ("outcome", "cpu_budget", "memory_budget")
        if any(amaru[field] != cardano[field] for field in fields) or any(
            row["outcome"] != row["expected_outcome"] for row in pair.values()
        ):
            mismatches.append({
                "script_name": script_name, "execution": execution,
                "amaru": {field: amaru[field] for field in fields},
                "cardano-node": {field: cardano[field] for field in fields},
            })

    distributions = {}
    for implementation in adapters:
        distributions[implementation] = {}
        for outcome in ("accepted", "rejected"):
            selected = [
                row for row in records
                if row["implementation"] == implementation and row["expected_outcome"] == outcome
            ]
            distributions[implementation][outcome] = _distribution(selected)

    raw_path = output / "raw-evaluations.ndjson"
    raw_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in records), encoding="utf-8")
    report = {
        "schema_version": "v1",
        "cost_model": {"path": model.name, "sha256": QUALIFIED_COST_MODEL_SHA256, "parameter_count": len(costs)},
        "executions_per_script": count,
        "records": records,
        "distributions": distributions,
        "mismatches": mismatches,
        "checks": {"plutus_result_and_budget_match": not mismatches},
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = [
        "# Plutus V2 evaluator conformance",
        "",
        f"Cost model: `sha256:{QUALIFIED_COST_MODEL_SHA256}` ({len(costs)} ordered parameters).",
        "",
        "Raw nanoseconds are retained. Human-facing microseconds preserve the fractional part.",
        "",
    ]
    for implementation in adapters:
        for outcome in ("accepted", "rejected"):
            d = distributions[implementation][outcome]
            md.append(f"- {implementation} {outcome}: n={d['sample_count']}, p50={d['p50_micros']:.3f} us, p95={d['p95_micros']:.3f} us")
    (output / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return report_path


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    report_path = run_plutus_conformance(config)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(json.dumps(report, sort_keys=True))
    return 0 if report["checks"]["plutus_result_and_budget_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
