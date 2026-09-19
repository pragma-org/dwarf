#!/usr/bin/env python3
"""Run one bounded real Cardano-node measurement workload leg."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.runtime_amaru_measurement_calibration import (
    HANDSHAKE_UNSUPPORTED_VERSION_HEX,
    RESPONSE_CAP_BYTES,
    build_attempt_record,
    run_attempts,
    summarize_attempts,
)
from profile_manager.measurement_collectors.cardano_patched import (
    CARDANO_MEASUREMENT_PATCH_SHA256,
    CARDANO_SOURCE_REVISION,
    paired_cardano_overhead_calibration,
)


SEED = "0xCA4DA001"
PLUTUS_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "corpora"
    / "cardano-measurement"
    / "always-succeeds-v2.plutus"
)
PLUTUS_FAIL_SCRIPT = PLUTUS_SCRIPT.with_name("always-fails-v2.plutus")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _physical_memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def build_workload_identity(
    *, attempt_count: int, seed: str = SEED,
    plutus_transactions: int = 0, epoch_observation_seconds: float = 0,
) -> dict[str, Any]:
    body = {
        "scenario_id": "cardano-measurement-calibration",
        "implementation": "cardano-node",
        "seed": seed,
        "attempt_count": int(attempt_count),
        "mini_protocol": "handshake",
        "case": "unsupported-version-refusal",
        "payload_hex": HANDSHAKE_UNSUPPORTED_VERSION_HEX,
        "network_magic": 42,
        "transport": "tcp",
        "target_port": 3001,
        "mux_direction_bit": 0,
        "response_cap_bytes": RESPONSE_CAP_BYTES,
        "plutus_transactions": int(plutus_transactions),
        "epoch_observation_seconds": float(epoch_observation_seconds),
        "plutus_script_sha256": (
            _sha256_bytes(PLUTUS_SCRIPT.read_bytes())
            if plutus_transactions
            else None
        ),
        "plutus_fail_script_sha256": (
            _sha256_bytes(PLUTUS_FAIL_SCRIPT.read_bytes())
            if plutus_transactions > 1
            else None
        ),
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {**body, "workload_digest": _sha256_bytes(encoded)}


def _result_context(output_dir: Path) -> dict[str, Any]:
    system = platform.uname()
    script = Path(__file__).resolve()
    return {
        "run_id": output_dir.name,
        "timing_policy": {
            "clock": "monotonic-perf-counter-ns",
            "start": "before-tcp-connect",
            "stop": "terminal-response-eof-timeout-or-disconnect",
            "warmup": "none",
            "outcome_inclusion": "all",
        },
        "hardware": {
            "system": system.system,
            "kernel_release": system.release,
            "architecture": system.machine,
            "logical_cpu_count": os.cpu_count(),
            "physical_memory_bytes": _physical_memory_bytes(),
        },
        "runner": {"script": script.name, "script_sha256": _sha256_bytes(script.read_bytes())},
    }


def _run(command: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)


def _checked(command: list[str], *, timeout: float = 60) -> subprocess.CompletedProcess:
    result = _run(command, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or f"command failed: {command[1]}"
        )
    return result


def _transaction_id(cli: str, transaction: Path) -> str:
    result = _checked(
        [cli, "conway", "transaction", "txid", "--tx-file", str(transaction)]
    )
    text = result.stdout.strip()
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return text
    return str(body.get("txhash") or "")


def _query_utxo(
    *, cli: str, socket_path: str, network_magic: int,
    address: str, destination: Path,
) -> dict[str, Any]:
    _checked([
        cli, "query", "utxo", "--address", address,
        "--socket-path", socket_path, "--testnet-magic", str(network_magic),
        "--out-file", str(destination),
    ])
    return json.loads(destination.read_text(encoding="utf-8"))


def _wait_for_utxo(
    *, cli: str, socket_path: str, network_magic: int, address: str,
    destination: Path, transaction_id: str, present: bool, timeout_seconds: float = 60,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter_ns()
    deadline = time.monotonic() + timeout_seconds
    while True:
        body = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=address, destination=destination,
        )
        observed = any(key.startswith(transaction_id + "#") for key in body)
        if observed is present:
            return body, (time.perf_counter_ns() - started) / 1_000
        if time.monotonic() >= deadline:
            state = "appear" if present else "be spent"
            raise RuntimeError(f"transaction {transaction_id} did not {state} before timeout")
        time.sleep(0.5)


def _wait_for_utxo_key_absent(
    *, cli: str, socket_path: str, network_magic: int, address: str,
    destination: Path, utxo_key: str, timeout_seconds: float = 60,
) -> float:
    started = time.perf_counter_ns()
    deadline = time.monotonic() + timeout_seconds
    while True:
        body = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=address, destination=destination,
        )
        if utxo_key not in body:
            return (time.perf_counter_ns() - started) / 1_000
        if time.monotonic() >= deadline:
            raise RuntimeError(f"collateral {utxo_key} was not consumed before timeout")
        time.sleep(0.5)


def run_plutus_workload(
    *, runtime_root: Path, runtime: dict[str, Any], node: dict[str, Any],
    output_dir: Path, transaction_count: int,
) -> dict[str, Any]:
    if transaction_count < 1 or transaction_count > 100:
        raise ValueError("plutus transaction count must be within 1..100")
    for source in (PLUTUS_SCRIPT, PLUTUS_FAIL_SCRIPT):
        if not source.is_file():
            raise RuntimeError(f"retained Plutus workload is missing: {source}")
    cli = str((runtime.get("support_binaries") or {}).get("cardano-cli") or "cardano-cli")
    socket_path = str(node.get("socket_path") or "")
    network_magic = int(runtime.get("network_magic", 42))
    if not socket_path or not Path(socket_path).exists():
        raise RuntimeError("Cardano-node measurement workload has no live host socket")
    key_root = runtime_root / "env" / "utxo-keys" / "utxo1"
    verification_key = key_root / "utxo.vkey"
    signing_key = key_root / "utxo.skey"
    if not verification_key.is_file() or not signing_key.is_file():
        raise RuntimeError("Cardano-node measurement workload has no genesis UTxO keys")
    work = output_dir / "plutus-workload"
    work.mkdir(parents=True, exist_ok=True)
    scripts = {
        "accepted": work / "always-succeeds-v2.plutus",
        "rejected": work / "always-fails-v2.plutus",
    }
    shutil.copy2(PLUTUS_SCRIPT, scripts["accepted"])
    shutil.copy2(PLUTUS_FAIL_SCRIPT, scripts["rejected"])
    payment_address = _checked([
        cli, "address", "build", "--testnet-magic", str(network_magic),
        "--payment-verification-key-file", str(verification_key),
    ]).stdout.strip()
    script_addresses = {
        outcome: _checked([
            cli, "address", "build", "--payment-script-file", str(script),
            "--testnet-magic", str(network_magic),
        ]).stdout.strip()
        for outcome, script in scripts.items()
    }
    records = []
    for index in range(transaction_count):
        expected_outcome = "accepted" if index % 2 == 0 else "rejected"
        script = scripts[expected_outcome]
        script_address = script_addresses[expected_outcome]
        regular = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=payment_address, destination=work / f"regular-{index}-before.json",
        )
        if not regular:
            raise RuntimeError("measurement payment address has no spendable UTxO")
        tx_in = max(
            regular,
            key=lambda key: int((regular[key].get("value") or {}).get("lovelace", 0)),
        )
        lock_body = work / f"lock-{index}.body"
        lock_tx = work / f"lock-{index}.tx"
        _checked([
            cli, "conway", "transaction", "build",
            "--testnet-magic", str(network_magic), "--socket-path", socket_path,
            "--change-address", payment_address, "--tx-in", tx_in,
            "--tx-out", f"{script_address}+5000000",
            "--tx-out-inline-datum-value", "42",
            "--tx-out", f"{payment_address}+5000000",
            "--out-file", str(lock_body),
        ], timeout=120)
        _checked([
            cli, "conway", "transaction", "sign", "--tx-body-file", str(lock_body),
            "--testnet-magic", str(network_magic), "--signing-key-file", str(signing_key),
            "--out-file", str(lock_tx),
        ])
        lock_started = time.perf_counter_ns()
        _checked([
            cli, "conway", "transaction", "submit", "--socket-path", socket_path,
            "--testnet-magic", str(network_magic), "--tx-file", str(lock_tx),
        ])
        lock_submit_us = (time.perf_counter_ns() - lock_started) / 1_000
        lock_id = _transaction_id(cli, lock_tx)
        script_utxo, lock_include_us = _wait_for_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=script_address, destination=work / f"script-{index}.json",
            transaction_id=lock_id, present=True,
        )
        script_tx_in = next(
            key for key in script_utxo if key.startswith(lock_id + "#")
        )
        regular = _query_utxo(
            cli=cli, socket_path=socket_path, network_magic=network_magic,
            address=payment_address, destination=work / f"regular-{index}-collateral.json",
        )
        collateral_candidates = [
            key for key, value in regular.items()
            if key.startswith(lock_id + "#")
            and int((value.get("value") or {}).get("lovelace", 0)) == 5_000_000
        ]
        if not collateral_candidates:
            raise RuntimeError("lock transaction did not create the bounded collateral output")
        collateral = collateral_candidates[0]
        spend_body = work / f"spend-{index}.body"
        spend_tx = work / f"spend-{index}.tx"
        build_started = time.perf_counter_ns()
        build_command = [
            cli, "conway", "transaction", "build",
            "--testnet-magic", str(network_magic), "--socket-path", socket_path,
            "--change-address", payment_address, "--tx-in", script_tx_in,
            "--tx-in-script-file", str(script), "--tx-in-inline-datum-present",
            "--tx-in-redeemer-value", "42", "--tx-in-collateral", collateral,
            "--tx-out", f"{payment_address}+3000000",
        ]
        if expected_outcome == "rejected":
            build_command.append("--script-invalid")
        build_command.extend(["--out-file", str(spend_body)])
        _checked(build_command, timeout=120)
        build_us = (time.perf_counter_ns() - build_started) / 1_000
        _checked([
            cli, "conway", "transaction", "sign", "--tx-body-file", str(spend_body),
            "--testnet-magic", str(network_magic), "--signing-key-file", str(signing_key),
            "--out-file", str(spend_tx),
        ])
        submit_started = time.perf_counter_ns()
        _checked([
            cli, "conway", "transaction", "submit", "--socket-path", socket_path,
            "--testnet-magic", str(network_magic), "--tx-file", str(spend_tx),
        ])
        submit_us = (time.perf_counter_ns() - submit_started) / 1_000
        spend_id = _transaction_id(cli, spend_tx)
        if expected_outcome == "accepted":
            _body, include_us = _wait_for_utxo(
                cli=cli, socket_path=socket_path, network_magic=network_magic,
                address=payment_address, destination=work / f"regular-{index}-after.json",
                transaction_id=spend_id, present=True,
            )
            chain_outcome = "included-valid"
        else:
            include_us = _wait_for_utxo_key_absent(
                cli=cli, socket_path=socket_path, network_magic=network_magic,
                address=payment_address,
                destination=work / f"regular-{index}-after.json",
                utxo_key=collateral,
            )
            chain_outcome = "included-invalid"
        records.append({
            "attempt_id": f"plutus-{index:04d}",
            "outcome": expected_outcome,
            "chain_outcome": chain_outcome,
            "lock_transaction_id": lock_id,
            "spend_transaction_id": spend_id,
            "lock_submit_us": lock_submit_us,
            "lock_submit_to_include_us": lock_include_us,
            "plutus_build_us": build_us,
            "plutus_submit_us": submit_us,
            "plutus_submit_to_include_us": include_us,
        })
    result = {
        "scripts": {
            outcome: {
                "artifact": f"plutus-workload/{script.name}",
                "sha256": _sha256_bytes(script.read_bytes()),
            }
            for outcome, script in scripts.items()
        },
        "transaction_count": transaction_count,
        "records": records,
    }
    (work / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _container_ip(container: str) -> str:
    result = _run(["docker", "inspect", container])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot inspect {container}")
    body = json.loads(result.stdout)[0]
    for network in ((body.get("NetworkSettings") or {}).get("Networks") or {}).values():
        if network.get("IPAddress"):
            return str(network["IPAddress"])
    raise RuntimeError(f"container has no routable address: {container}")


def _release(runtime: dict[str, Any]) -> dict[str, Any]:
    snapshot = ((runtime.get("version_provenance") or {}).get("catalog_snapshot") or {})
    for release in snapshot.get("selected_releases") or []:
        if release.get("implementation") == "cardano-node":
            return release
    raise RuntimeError("runtime metadata has no exact Cardano-node release")


def _target(runtime: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    release = _release(runtime)
    node = next(
        (node for node in (runtime.get("nodes") or []) if node.get("impl") == "cardano-node"),
        None,
    )
    if node is None:
        raise RuntimeError("runtime metadata has no Cardano-node target")
    artifact = node.get("artifact_identity") or {}
    version = node.get("version_identity") or {}
    stock_digest = next(
        item["digest"]
        for item in release.get("artifacts") or []
        if item.get("kind") == "oci" and item.get("availability") == "available"
    )
    observed_digest = artifact.get("image_digest") or artifact.get("container_image_id")
    if artifact.get("satisfied") is not True or version.get("satisfied") is not True:
        raise RuntimeError("Cardano-node runtime identity is not proven")
    if node.get("source_revision") != release.get("source_revision"):
        raise RuntimeError("Cardano-node runtime identity does not match the catalog")
    mode = str(node.get("target_mode") or "stock")
    if mode == "patched":
        if release.get("source_revision") != CARDANO_SOURCE_REVISION:
            raise RuntimeError("Cardano-node patched source revision does not match")
        if node.get("patch_set_sha256") != CARDANO_MEASUREMENT_PATCH_SHA256:
            raise RuntimeError("Cardano-node patched patch-set identity does not match")
        patched_digest = str(node.get("image_digest") or observed_digest or "")
        if observed_digest != patched_digest:
            raise RuntimeError("Cardano-node patched image identity does not match runtime")
        for field in (
            "executable_digest", "build_result_sha256", "runtime_probe_log_sha256"
        ):
            value = str(node.get(field) or "")
            if not value.startswith("sha256:") or len(value) != 71:
                raise RuntimeError(f"Cardano-node patched {field} is not immutable")
        if not str(node.get("image_ref") or "").endswith("@" + patched_digest):
            raise RuntimeError("Cardano-node patched image reference is not digest-pinned")
        return ({
            "implementation": "cardano-node",
            "version": release["version"],
            "source_revision": release["source_revision"],
            "mode": "patched",
            "patch_set_sha256": node["patch_set_sha256"],
            "image_digest": patched_digest,
            "image_reference": node.get("image_ref"),
            "executable_digest": node["executable_digest"],
            "build_result_sha256": node["build_result_sha256"],
            "runtime_probe_log_sha256": node["runtime_probe_log_sha256"],
        }, node)
    if mode != "stock":
        raise RuntimeError(f"unsupported Cardano-node target mode: {mode}")
    if observed_digest != stock_digest:
        raise RuntimeError("Cardano-node runtime identity does not match the catalog")
    return ({
        "implementation": "cardano-node",
        "version": release["version"],
        "source_revision": release["source_revision"],
        "mode": "stock",
        "image_digest": stock_digest,
        "image_reference": node.get("image_ref"),
    }, node)


def _capture_logs(container: str, *, since: str, destination: Path) -> dict[str, Any]:
    result = _run(["docker", "logs", "--since", since, container], timeout=120)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = result.stdout + ("\n" + result.stderr if result.stderr else "")
    destination.write_text(body, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot capture logs from {container}")
    record_count = 0
    protocol_record_count = 0
    namespaces: set[str] = set()
    for line in body.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        record_count += 1
        namespace = record.get("ns")
        if isinstance(namespace, str):
            namespaces.add(namespace)
            if namespace.startswith(
                ("Net.Handshake.", "ChainSync.", "BlockFetch.", "TxSubmission.", "Mempool")
            ) or namespace.startswith("ChainDB."):
                protocol_record_count += 1
    return {
        "record_count": record_count,
        "protocol_record_count": protocol_record_count,
        "byte_count": len(body.encode("utf-8")),
        "namespaces": sorted(namespaces),
    }


def run_leg(
    *,
    runtime_root: Path,
    output_dir: Path,
    attempt_count: int,
    timeout_seconds: float,
    observation_seconds: float = 2.0,
    trace_timeout_seconds: float = 20.0,
    plutus_transactions: int = 0,
    epoch_observation_seconds: float = 0,
) -> dict[str, Any]:
    runtime = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    target, node = _target(runtime)
    container = str(node.get("container_name") or "")
    if not container:
        raise RuntimeError("Cardano-node target has no container identity")
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    attempts = run_attempts(
        host=_container_ip(container),
        port=3001,
        attempt_count=attempt_count,
        timeout_seconds=timeout_seconds,
    )
    attempts_path = output_dir / "attempts.ndjson"
    attempts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in attempts),
        encoding="utf-8",
    )
    plutus_workload = (
        run_plutus_workload(
            runtime_root=runtime_root,
            runtime=runtime,
            node=node,
            output_dir=output_dir,
            transaction_count=plutus_transactions,
        )
        if plutus_transactions
        else None
    )
    if epoch_observation_seconds:
        time.sleep(epoch_observation_seconds)
    trace_path = output_dir / "raw" / "node1.ndjson"
    observation_started = time.monotonic()
    time.sleep(observation_seconds)
    while True:
        trace_evidence = _capture_logs(
            container, since=started_at, destination=trace_path
        )
        if trace_evidence["protocol_record_count"] > 0:
            break
        elapsed = time.monotonic() - observation_started
        if elapsed >= trace_timeout_seconds:
            break
        time.sleep(min(1.0, trace_timeout_seconds - elapsed))
    observed_seconds = time.monotonic() - observation_started
    summarized = summarize_attempts(attempts)
    result = {
        "schema_version": 1,
        "created_at": _utc_now(),
        **_result_context(output_dir),
        "target": target,
        "workload_identity": build_workload_identity(
            attempt_count=attempt_count,
            plutus_transactions=plutus_transactions,
            epoch_observation_seconds=epoch_observation_seconds,
        ),
        "measurements": {"handshake_rejection_roundtrip": summarized["combined"]},
        "measurements_by_outcome": {"handshake_rejection_roundtrip": summarized["by_outcome"]},
        "attempts": {
            "total": len(attempts),
            "outcomes": {
                outcome: sum(row["outcome"] == outcome for row in attempts)
                for outcome in sorted({row["outcome"] for row in attempts})
            },
            "artifact": "attempts.ndjson",
        },
        "raw_trace": "raw/node1.ndjson",
        "plutus_workload": plutus_workload,
        "node_trace": {
            **trace_evidence,
            "minimum_observation_seconds": observation_seconds,
            "observation_timeout_seconds": trace_timeout_seconds,
            "observed_seconds": observed_seconds,
        },
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    leg = sub.add_parser("leg")
    leg.add_argument("--runtime-root", type=Path, required=True)
    leg.add_argument("--output-dir", type=Path, required=True)
    leg.add_argument("--attempts", type=int, default=100)
    leg.add_argument("--timeout-seconds", type=float, default=2.0)
    leg.add_argument("--observation-seconds", type=float, default=2.0)
    leg.add_argument("--trace-timeout-seconds", type=float, default=20.0)
    leg.add_argument("--plutus-transactions", type=int, default=0)
    leg.add_argument("--epoch-observation-seconds", type=float, default=0)
    pair = sub.add_parser("pair")
    pair.add_argument("--stock", type=Path, required=True)
    pair.add_argument("--patched", type=Path, required=True)
    pair.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "leg":
        if args.attempts < 30 or args.attempts > 10_000:
            raise SystemExit("--attempts must be within 30..10000")
        if args.observation_seconds < 0.5 or args.observation_seconds > 30:
            raise SystemExit("--observation-seconds must be within 0.5..30")
        if (
            args.trace_timeout_seconds < args.observation_seconds
            or args.trace_timeout_seconds > 120
        ):
            raise SystemExit(
                "--trace-timeout-seconds must be at least observation-seconds and at most 120"
            )
        if args.plutus_transactions < 0 or args.plutus_transactions > 100:
            raise SystemExit("--plutus-transactions must be within 0..100")
        if args.epoch_observation_seconds < 0 or args.epoch_observation_seconds > 120:
            raise SystemExit("--epoch-observation-seconds must be within 0..120")
        result = run_leg(
            runtime_root=args.runtime_root,
            output_dir=args.output_dir,
            attempt_count=args.attempts,
            timeout_seconds=args.timeout_seconds,
            observation_seconds=args.observation_seconds,
            trace_timeout_seconds=args.trace_timeout_seconds,
            plutus_transactions=args.plutus_transactions,
            epoch_observation_seconds=args.epoch_observation_seconds,
        )
        trace = result["node_trace"]
        status = trace["record_count"] > 0 and trace["protocol_record_count"] > 0
    else:
        stock = json.loads(args.stock.read_text(encoding="utf-8"))
        patched = json.loads(args.patched.read_text(encoding="utf-8"))
        result = paired_cardano_overhead_calibration(
            stock, patched, minimum_samples=30
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        status = result["status"] == "available"
    print(json.dumps(result, sort_keys=True))
    return 0 if status else 2


if __name__ == "__main__":
    raise SystemExit(main())
