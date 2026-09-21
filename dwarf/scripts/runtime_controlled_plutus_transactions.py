#!/usr/bin/env python3
"""Submit the frozen valid and invalid Plutus V2 transactions to a live DWARF runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from scripts.runtime_amaru_measurement_calibration import extract_latest_adopted_tip
from scripts.runtime_cardano_measurement_calibration import (
    _container_state,
    _query_cardano_tip,
    _run,
    _target,
    classify_log_signals,
    run_plutus_workload,
)


def _container_logs(container: str, *, since: str) -> str:
    result = _run(["docker", "logs", "--since", since, container], timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot read logs from {container}")
    return result.stdout + ("\n" + result.stderr if result.stderr else "")


def _health_target(
    runtime: dict, transaction_producer: dict, measurement_implementation: str
) -> dict:
    network_magic = int(runtime.get("network_magic") or 42)
    if measurement_implementation == "cardano-node":
        return {
            "implementation": "cardano-node",
            "container": str(transaction_producer["container_name"]),
            "socket_path": str(
                transaction_producer.get("container_socket_path") or "/state/node.socket"
            ),
            "network_magic": network_magic,
        }
    if measurement_implementation == "amaru":
        services = ((runtime.get("identity") or {}).get("services") or {})
        container = (services.get("amaru-relay-1") or {}).get("container")
        if not container:
            raise RuntimeError("runtime metadata has no measured Amaru target")
        return {
            "implementation": "amaru",
            "container": str(container),
            "network_magic": network_magic,
        }
    raise RuntimeError(
        f"unsupported measurement implementation: {measurement_implementation}"
    )


def _target_tip(target: dict) -> dict | None:
    if target["implementation"] == "cardano-node":
        return _query_cardano_tip(
            target["container"],
            socket_path=target["socket_path"],
            network_magic=target["network_magic"],
        )
    result = _run(
        ["docker", "logs", "--tail", "12000", target["container"]], timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"cannot read logs from {target['container']}"
        )
    return extract_latest_adopted_tip(
        result.stdout + ("\n" + result.stderr if result.stderr else "")
    )


def _copy_fixture_key(container: str, source: str, destination: Path) -> None:
    result = _run(["docker", "cp", f"{container}:{source}", str(destination)], timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot copy fixture key from {container}")
    destination.chmod(0o600)


def _prepare_amaru_transaction_producer(
    runtime: dict, output_dir: Path, stack: ExitStack
) -> tuple[dict, dict]:
    services = ((runtime.get("identity") or {}).get("services") or {})
    producer = services.get("p1") or {}
    if producer.get("matched") is not True or not producer.get("container"):
        raise RuntimeError("Amaru runtime has no qualified Cardano producer")
    project = str(runtime.get("compose_project") or "")
    if not project:
        raise RuntimeError("Amaru runtime has no Compose project identity")
    temp_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="dwarf-plutus-")))
    key_root = temp_root / "keys"
    key_root.mkdir(mode=0o700)
    configurator = f"{project}-configurator-1"
    _copy_fixture_key(
        configurator, "/tmp/testnet/utxos/keys/genesis.1.vkey", key_root / "utxo.vkey"
    )
    _copy_fixture_key(
        configurator, "/tmp/testnet/utxos/keys/genesis.1.skey", key_root / "utxo.skey"
    )
    image = str(producer.get("expected_image") or "")
    if "@sha256:" not in image:
        raise RuntimeError("Cardano producer image is not digest-pinned")
    wrapper = temp_root / "cardano-cli"
    host_work = str(output_dir.resolve())
    host_keys = str(key_root.resolve())
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"HOST_WORK={shlex.quote(host_work)}\n"
        f"HOST_KEYS={shlex.quote(host_keys)}\n"
        f"PRODUCER={shlex.quote(str(producer['container']))}\n"
        f"IMAGE={shlex.quote(image)}\n"
        "args=()\n"
        "for arg in \"$@\"; do\n"
        "  arg=$(printf '%s' \"$arg\" | sed \"s|$HOST_WORK|/work|;s|$HOST_KEYS|/keys|\")\n"
        "  args+=(\"$arg\")\n"
        "done\n"
        "if docker run --rm --volumes-from \"$PRODUCER\" "
        "--mount \"type=bind,src=$HOST_WORK,dst=/work\" "
        "--mount \"type=bind,src=$HOST_KEYS,dst=/keys,readonly\" "
        "\"$IMAGE\" cli \"$" "{args[@]}\"; then\n"
        "  status=0\n"
        "else\n"
        "  status=$?\n"
        "fi\n"
        "docker run --rm --mount \"type=bind,src=$HOST_WORK,dst=/work\" "
        "--entrypoint chown \"$IMAGE\" -R \"$(id -u):$(id -g)\" /work\n"
        "exit \"$status\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    workload_runtime = dict(runtime)
    workload_runtime["network_magic"] = 42
    workload_runtime["support_binaries"] = {"cardano-cli": str(wrapper)}
    workload_runtime["workload_key_root"] = str(key_root)
    workload_runtime["workload_socket_remote"] = True
    return workload_runtime, {
        "container_name": str(producer["container"]),
        "container_socket_path": "/state/node.socket",
        "socket_path": "/state/node.socket",
    }


def _retain_plutus_v2_topology(runtime: dict, destination: Path) -> dict:
    topology = json.loads(json.dumps(runtime.get("plutus_v2") or {}))
    if topology.get("verified") is not True:
        raise RuntimeError("Amaru workload requires verified live PlutusV2 topology evidence")
    records = [topology.get("pinned_cost_model") or {}]
    records.extend(topology.get("generated_genesis") or [])
    records.append(topology.get("live_protocol_parameters") or {})
    if len(records) != 8:
        raise RuntimeError("live PlutusV2 topology evidence is incomplete")
    destination.mkdir(parents=True, exist_ok=False)
    names = ["pinned-cost-model.json"]
    names.extend(Path(str(record.get("path") or "")).name for record in records[1:-1])
    names.append("live-protocol-parameters.json")
    for record, name in zip(records, names, strict=True):
        source = Path(str(record.get("path") or ""))
        expected = str(record.get("sha256") or "")
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"PlutusV2 topology evidence digest mismatch: {source}")
        retained = destination / name
        shutil.copy2(source, retained)
        record["path"] = str(retained)
    topology["pinned_cost_model"] = records[0]
    topology["generated_genesis"] = records[1:7]
    topology["live_protocol_parameters"] = records[7]
    return topology


def _plutus_workload_accounting(records: list[dict], implementation: str) -> dict:
    """Normalize every Plutus spend attempt for the existing external collector."""
    attempts = []
    for record in records:
        required = (
            "attempt_id", "outcome", "spend_transaction_id",
            "transaction_bytes", "elapsed_nanos", "elapsed_micros",
            "submit_to_block_inclusion_nanos", "submit_to_block_inclusion_micros",
        )
        missing = [key for key in required if record.get(key) is None]
        if missing:
            raise RuntimeError(
                "Plutus workload accounting is incomplete for "
                f"{record.get('attempt_id', '<unknown>')}: {', '.join(missing)}"
            )
        attempt = {
            "input_id": str(record["attempt_id"]),
            "tx_id": str(record["spend_transaction_id"]),
            "outcome": str(record["outcome"]),
            "bytes": int(record["transaction_bytes"]),
            "elapsed_nanos": int(record["elapsed_nanos"]),
            "elapsed_micros": float(record["elapsed_micros"]),
            "submit_to_block_inclusion_nanos": int(
                record["submit_to_block_inclusion_nanos"]
            ),
            "submit_to_block_inclusion_micros": float(
                record["submit_to_block_inclusion_micros"]
            ),
        }
        if implementation == "cardano-node":
            for stage in ("submit_to_protocol_response", "submit_to_chain_adoption"):
                attempt[f"{stage}_nanos"] = int(record[f"{stage}_nanos"])
                attempt[f"{stage}_micros"] = float(record[f"{stage}_micros"])
        attempts.append(attempt)
    accepted = sum(row["outcome"] == "accepted" for row in attempts)
    rejected = sum(row["outcome"] == "rejected" for row in attempts)
    timed_out = sum(row["outcome"] in {"timeout", "timed_out"} for row in attempts)
    return {
        "attempted": len(attempts),
        "successful": accepted,
        "rejected": rejected,
        "timed_out": timed_out,
        "bytes": sum(row["bytes"] for row in attempts),
        "batches": 1,
        "backlog": 0,
        "attempts": attempts,
    }


def run_controlled_plutus_transactions(
    *,
    runtime_root: Path,
    output_dir: Path,
    transaction_count: int,
    measurement_implementation: str = "cardano-node",
) -> dict:
    if transaction_count != 60:
        raise ValueError("transaction_count must be exactly 60")
    runtime = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    if (
        measurement_implementation == "amaru"
        and (runtime.get("plutus_v2") or {}).get("verified") is not True
    ):
        raise RuntimeError("Amaru workload requires verified live PlutusV2 topology evidence")
    output_dir.mkdir(parents=True, exist_ok=False)
    retained_topology = None
    if measurement_implementation == "amaru":
        retained_topology = _retain_plutus_v2_topology(
            runtime, output_dir / "topology-evidence"
        )
    with ExitStack() as stack:
        if measurement_implementation == "amaru":
            workload_runtime, node = _prepare_amaru_transaction_producer(
                runtime, output_dir, stack
            )
        else:
            _target_identity, node = _target(runtime)
            workload_runtime = runtime
        health_target = _health_target(runtime, node, measurement_implementation)
        container = health_target["container"]
        started_at = datetime.now(timezone.utc).isoformat()
        before = _container_state(container)
        tip_before = _target_tip(health_target)
        result = run_plutus_workload(
            runtime_root=runtime_root,
            runtime=workload_runtime,
            node=node,
            output_dir=output_dir,
            transaction_count=transaction_count,
        )
        after = _container_state(container)
        tip_after = _target_tip(health_target)
        result["target_health"] = {
            "before": before,
            "after": after,
            "tip_before": tip_before,
            "tip_after": tip_after,
            "log_signals": classify_log_signals(
                _container_logs(container, since=started_at)
            ),
        }
        if retained_topology is not None:
            result["plutus_v2_topology"] = retained_topology
    accepted = sum(
        row["outcome"] == "accepted" and row["chain_outcome"] == "included-valid"
        for row in result["records"]
    )
    rejected = sum(
        row["outcome"] == "rejected" and row["chain_outcome"] == "included-invalid"
        for row in result["records"]
    )
    identifiers_retained = all(
        all(row.get(key) for key in ("attempt_id", "lock_transaction_id", "spend_transaction_id"))
        for row in result["records"]
    )
    health = result["target_health"]
    before_height = (health.get("tip_before") or {}).get("block_height")
    after_height = (health.get("tip_after") or {}).get("block_height")
    progress = (
        isinstance(before_height, int)
        and isinstance(after_height, int)
        and after_height > before_height
    )
    before_state = health.get("before") or {}
    after_state = health.get("after") or {}
    health_clean = (
        before_state.get("running") is True
        and after_state.get("running") is True
        and after_state.get("oom_killed") is not True
        and int(after_state.get("restart_count") or 0)
        == int(before_state.get("restart_count") or 0)
        and not (health.get("log_signals") or {}).get("fatal")
    )
    result["checks"] = {
        "plutus_live_outcomes_observed": accepted >= 30 and rejected >= 30,
        "transaction_identifiers_retained": identifiers_retained,
        "target_progress_continues": progress,
        "target_health_clean": health_clean,
    }
    result["outcome_counts"] = {"accepted": accepted, "rejected": rejected}
    result["accounting"] = _plutus_workload_accounting(
        result["records"], measurement_implementation
    )
    result["checks"]["workload_accounting_complete"] = (
        result["accounting"]["attempted"] == transaction_count
        and len(result["accounting"]["attempts"]) == transaction_count
        and result["accounting"]["successful"] == accepted
        and result["accounting"]["rejected"] == rejected
        and result["accounting"]["timed_out"] == 0
    )
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--transaction-count", type=int, default=60)
    parser.add_argument(
        "--measurement-implementation",
        choices=("amaru", "cardano-node"),
        required=True,
    )
    args = parser.parse_args(argv)
    if args.transaction_count != 60:
        raise SystemExit("--transaction-count must be exactly 60")
    result = run_controlled_plutus_transactions(
        runtime_root=args.runtime_root,
        output_dir=args.output_dir,
        transaction_count=args.transaction_count,
        measurement_implementation=args.measurement_implementation,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if all(result["checks"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
