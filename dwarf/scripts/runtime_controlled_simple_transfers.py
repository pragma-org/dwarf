#!/usr/bin/env python3
"""Submit a frozen set of real signed simple-payment transactions."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.runtime_cardano_measurement_calibration import (
    _checked,
    _container_state,
    _query_utxo,
    _run,
    _target,
    _transaction_id,
    _wait_for_utxo,
    classify_log_signals,
)
from scripts.runtime_controlled_plutus_transactions import (
    _container_logs,
    _health_target,
    _prepare_amaru_transaction_producer,
    _target_tip,
)


DATASET = (
    Path(__file__).resolve().parents[1]
    / "corpora/cardano-measurement/simple-transfer-v1.json"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def accounting_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = []
    for record in records:
        row = {
            "input_id": str(record["attempt_id"]),
            "outcome": str(record["outcome"]),
            "elapsed_nanos": int(record["elapsed_nanos"]),
            "elapsed_micros": int(record["elapsed_nanos"]) / 1_000,
            "bytes": int(record["transaction_bytes"]),
        }
        if record.get("transaction_id"):
            row["tx_id"] = str(record["transaction_id"])
        for stage in (
            "submit_to_protocol_response",
            "submit_to_mempool_visibility",
            "submit_to_block_inclusion",
            "submit_to_chain_adoption",
        ):
            nanos = record.get(f"{stage}_nanos")
            if nanos is not None:
                row[f"{stage}_nanos"] = int(nanos)
                row[f"{stage}_micros"] = int(nanos) / 1_000
        attempts.append(row)
    return {
        "attempted": len(records),
        "successful": sum(row["outcome"] == "accepted" for row in records),
        "rejected": sum(row["outcome"] == "rejected" for row in records),
        "timed_out": sum(row["outcome"] == "timeout" for row in records),
        "bytes": sum(int(row["transaction_bytes"]) for row in records),
        "batches": 1,
        "backlog": 0,
        "attempts": attempts,
    }


def _load_dataset(attempt_count: int) -> dict[str, Any]:
    body = json.loads(DATASET.read_text(encoding="utf-8"))
    identity = dict(body)
    expected = identity.pop("dataset_sha256")
    digest = "sha256:" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if expected != digest:
        raise RuntimeError("simple-transfer dataset digest does not match its content")
    if attempt_count != int(body["attempt_count"]):
        raise ValueError("attempt_count must match the frozen dataset")
    return body


def _simple_transfer_attempts(
    *, runtime_root: Path, runtime: dict[str, Any], node: dict[str, Any],
    output_dir: Path, attempt_count: int, accepted_count: int,
    outcome_timeout_seconds: float,
) -> list[dict[str, Any]]:
    cli = str((runtime.get("support_binaries") or {}).get("cardano-cli") or "cardano-cli")
    socket_path = str(node.get("socket_path") or node.get("container_socket_path") or "")
    network_magic = int(runtime.get("network_magic", 42))
    if not socket_path or (
        runtime.get("workload_socket_remote") is not True and not Path(socket_path).exists()
    ):
        raise RuntimeError("simple-transfer workload has no live node socket")
    key_root = Path(runtime.get("workload_key_root") or runtime_root / "env/utxo-keys/utxo1")
    verification_key = key_root / str(runtime.get("workload_vkey_name") or "utxo.vkey")
    signing_key = key_root / str(runtime.get("workload_skey_name") or "utxo.skey")
    if not verification_key.is_file() or not signing_key.is_file():
        raise RuntimeError("simple-transfer workload has no genesis UTxO keys")
    work = output_dir / "transactions"
    work.mkdir(parents=True, exist_ok=True)
    address = _checked([
        cli, "address", "build", "--testnet-magic", str(network_magic),
        "--payment-verification-key-file", str(verification_key),
    ]).stdout.strip()
    records = []
    for index in range(attempt_count):
        expected_outcome = "accepted" if index < accepted_count else "rejected"
        if expected_outcome == "accepted":
            available = _query_utxo(
                cli=cli, socket_path=socket_path, network_magic=network_magic,
                address=address, destination=work / f"utxo-{index:04d}-before.json",
            )
            if not available:
                raise RuntimeError("simple-transfer payment address has no spendable UTxO")
            tx_in = max(
                available,
                key=lambda key: int((available[key].get("value") or {}).get("lovelace", 0)),
            )
            body_path = work / f"simple-transfer-{index:04d}.body"
            tx_path = work / f"simple-transfer-{index:04d}.tx"
            _checked([
                cli, "conway", "transaction", "build",
                "--testnet-magic", str(network_magic), "--socket-path", socket_path,
                "--change-address", address, "--tx-in", tx_in,
                "--tx-out", f"{address}+2000000", "--out-file", str(body_path),
            ], timeout=120)
            _checked([
                cli, "conway", "transaction", "sign", "--tx-body-file", str(body_path),
                "--testnet-magic", str(network_magic), "--signing-key-file", str(signing_key),
                "--out-file", str(tx_path),
            ])
        else:
            duplicate_index = index - accepted_count
            tx_path = work / f"simple-transfer-{duplicate_index:04d}.tx"
        tx_id = _transaction_id(cli, tx_path)
        started_at = _utc_now()
        started_ns = time.perf_counter_ns()
        submit_command = [
            cli, "conway", "transaction", "submit", "--socket-path", socket_path,
            "--testnet-magic", str(network_magic), "--tx-file", str(tx_path),
        ]
        try:
            submit = _run(submit_command, timeout=outcome_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            elapsed_nanos = time.perf_counter_ns() - started_ns
            records.append({
                "attempt_id": f"simple-transfer-{index:04d}",
                "transaction_id": tx_id,
                "transaction_bytes": tx_path.stat().st_size,
                "started_at": started_at,
                "ended_at": _utc_now(),
                "outcome": "timeout",
                "expected_outcome": expected_outcome,
                "outcome_reason": str(error),
                "elapsed_nanos": elapsed_nanos,
                "elapsed_micros": elapsed_nanos / 1_000,
                "delivery_response_nanos": None,
                "delivery_response_micros": None,
                "submit_to_inclusion_nanos": None,
                "submit_to_inclusion_micros": None,
                "submit_stdout": "",
            })
            continue
        response_ns = time.perf_counter_ns() - started_ns
        if submit.returncode != 0:
            elapsed_nanos = time.perf_counter_ns() - started_ns
            records.append({
                "attempt_id": f"simple-transfer-{index:04d}",
                "transaction_id": tx_id,
                "transaction_bytes": tx_path.stat().st_size,
                "started_at": started_at,
                "ended_at": _utc_now(),
                "outcome": "rejected",
                "expected_outcome": expected_outcome,
                "outcome_reason": submit.stderr.strip() or submit.stdout.strip(),
                "elapsed_nanos": elapsed_nanos,
                "elapsed_micros": elapsed_nanos / 1_000,
                "delivery_response_nanos": response_ns,
                "delivery_response_micros": response_ns / 1_000,
                "submit_to_inclusion_nanos": None,
                "submit_to_inclusion_micros": None,
                "submit_stdout": submit.stdout.strip(),
            })
            continue
        try:
            _body, include_us = _wait_for_utxo(
                cli=cli, socket_path=socket_path, network_magic=network_magic,
                address=address, destination=work / f"utxo-{index:04d}-after.json",
                transaction_id=tx_id, present=True,
                timeout_seconds=outcome_timeout_seconds,
            )
            outcome = "accepted"
            elapsed_nanos = time.perf_counter_ns() - started_ns
            reason = None
        except RuntimeError as error:
            outcome = "timeout"
            elapsed_nanos = time.perf_counter_ns() - started_ns
            include_us = None
            reason = str(error)
        record = {
            "attempt_id": f"simple-transfer-{index:04d}",
            "transaction_id": tx_id,
            "transaction_bytes": tx_path.stat().st_size,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "outcome": outcome,
            "expected_outcome": expected_outcome,
            "outcome_reason": reason,
            "elapsed_nanos": elapsed_nanos,
            "elapsed_micros": elapsed_nanos / 1_000,
            "delivery_response_nanos": response_ns,
            "delivery_response_micros": response_ns / 1_000,
            "submit_to_inclusion_nanos": (
                int(include_us * 1_000) if include_us is not None else None
            ),
            "submit_to_inclusion_micros": include_us,
            "submit_stdout": submit.stdout.strip(),
        }
        records.append(record)
    return records


def run_controlled_simple_transfers(
    *, runtime_root: Path, output_dir: Path, attempt_count: int,
    measurement_implementation: str, outcome_timeout_seconds: float = 90,
) -> dict[str, Any]:
    dataset = _load_dataset(attempt_count)
    runtime = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=False)
    with ExitStack() as stack:
        if measurement_implementation == "amaru":
            workload_runtime, node = _prepare_amaru_transaction_producer(runtime, output_dir, stack)
        elif measurement_implementation == "cardano-node":
            _identity, node = _target(runtime)
            workload_runtime = runtime
        else:
            raise ValueError("unsupported measurement implementation")
        target = _health_target(runtime, node, measurement_implementation)
        started_at = _utc_now()
        state_before = _container_state(target["container"])
        tip_before = _target_tip(target)
        records = _simple_transfer_attempts(
            runtime_root=runtime_root, runtime=workload_runtime, node=node,
            output_dir=output_dir, attempt_count=attempt_count,
            accepted_count=int(dataset["expected_outcomes"]["accepted_minimum"]),
            outcome_timeout_seconds=outcome_timeout_seconds,
        )
        state_after = _container_state(target["container"])
        tip_after = _target_tip(target)
        target_logs = _container_logs(target["container"], since=started_at)
    log_path = output_dir / "target.log"
    log_path.write_text(target_logs, encoding="utf-8")
    accepted_ids = [row["transaction_id"] for row in records if row["outcome"] == "accepted"]
    observed_ids = [tx_id for tx_id in accepted_ids if tx_id and tx_id in target_logs]
    for record in records:
        include_nanos = record.get("submit_to_inclusion_nanos")
        if include_nanos is not None:
            record["submit_to_block_inclusion_nanos"] = include_nanos
            record["submit_to_block_inclusion_micros"] = include_nanos / 1_000
        if measurement_implementation == "cardano-node":
            response_nanos = record.get("delivery_response_nanos")
            if response_nanos is not None:
                record["submit_to_protocol_response_nanos"] = response_nanos
                record["submit_to_protocol_response_micros"] = response_nanos / 1_000
            if include_nanos is not None:
                record["submit_to_chain_adoption_nanos"] = include_nanos
                record["submit_to_chain_adoption_micros"] = include_nanos / 1_000
    before_height = (tip_before or {}).get("block_height")
    after_height = (tip_after or {}).get("block_height")
    progress = isinstance(before_height, int) and isinstance(after_height, int) and after_height > before_height
    accounting = accounting_payload(records)
    checks = {
        "all_attempts_retained": len(records) == attempt_count,
        "minimum_real_attempts": len(records) >= 30,
        "successful_transfers_observed": accounting["successful"] >= 30,
        "expected_outcomes_observed": (
            accounting["successful"] >= int(dataset["expected_outcomes"]["accepted_minimum"])
            and accounting["rejected"] >= int(dataset["expected_outcomes"]["rejected_minimum"])
            and accounting["timed_out"] <= int(dataset["expected_outcomes"]["timed_out_maximum"])
        ),
        "every_attempt_has_known_outcome_duration": all(
            row["outcome"] in {"accepted", "rejected", "timeout"}
            and int(row["elapsed_nanos"]) >= 0 for row in records
        ),
        "target_progress_continues": progress,
        "target_health_clean": (
            state_before.get("running") is True
            and state_after.get("running") is True
            and state_after.get("oom_killed") is not True
            and int(state_after.get("restart_count") or 0) == int(state_before.get("restart_count") or 0)
            and not classify_log_signals(target_logs).get("fatal")
        ),
        "measured_target_transaction_correlation": (
            len(observed_ids) >= 1 if measurement_implementation == "amaru" else accounting["successful"] >= 30
        ),
    }
    result = {
        "schema_version": "v1",
        "measurement_implementation": measurement_implementation,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "dataset": {**dataset, "path": str(DATASET), "file_sha256": _sha256(DATASET)},
        "records": records,
        "accounting": accounting,
        "outcome_counts": {
            "attempted": accounting["attempted"],
            "accepted": accounting["successful"],
            "rejected": accounting["rejected"],
            "timed_out": accounting["timed_out"],
        },
        "target_correlation": {
            "accepted_transaction_ids": accepted_ids,
            "observed_transaction_ids": observed_ids,
            "observed_count": len(observed_ids),
            "method": "target-log-exact-transaction-id" if measurement_implementation == "amaru" else "direct-node-submit-and-utxo-inclusion",
        },
        "target_health": {
            "before": state_before, "after": state_after,
            "tip_before": tip_before, "tip_after": tip_after,
            "log_signals": classify_log_signals(target_logs),
        },
        "stage_availability": {
            "submit_to_protocol_response": {
                "status": "available" if measurement_implementation == "cardano-node" else "unavailable",
                "reason": None if measurement_implementation == "cardano-node" else "The support producer response is not an Amaru protocol-response boundary.",
            },
            "submit_to_mempool_visibility": {
                "status": "unavailable",
                "reason": "The workload retained no authoritative target mempool boundary; a configured or zero-event collector is not evidence.",
            },
            "submit_to_block_inclusion": {"status": "available", "reason": None},
            "submit_to_chain_adoption": {
                "status": "available" if measurement_implementation == "cardano-node" else "unavailable",
                "reason": None if measurement_implementation == "cardano-node" else "The Amaru target log proves transaction validation during adopted-block processing, but it does not share the workload monotonic clock.",
            },
            "target_chain_adoption_evidence": {
                "status": "available",
                "reason": "Exact target transaction identifiers were retained in adopted-chain evidence.",
                "correlated_transaction_count": len(observed_ids) if measurement_implementation == "amaru" else accounting_payload(records)["successful"],
            },
        },
        "checks": checks,
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--attempt-count", type=int, required=True)
    parser.add_argument("--measurement-implementation", choices=("amaru", "cardano-node"), required=True)
    parser.add_argument("--outcome-timeout-seconds", type=float, default=90)
    args = parser.parse_args(argv)
    result = run_controlled_simple_transfers(
        runtime_root=args.runtime_root, output_dir=args.output_dir,
        attempt_count=args.attempt_count,
        measurement_implementation=args.measurement_implementation,
        outcome_timeout_seconds=args.outcome_timeout_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if all(result["checks"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
