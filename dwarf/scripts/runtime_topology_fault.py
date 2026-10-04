from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _role_map(metadata: dict) -> dict[str, str]:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    return {
        str(node.get("id") or node.get("name")): str(node.get("role", "honest"))
        for node in nodes
        if str(node.get("id") or node.get("name"))
    }


def apply_topology_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    valid = {
        "simulate_peer_set_capture",
        "inject_hot_warm_churn",
        "perturb_ledger_peer_weights",
        "substitute_big_ledger_peers",
    }
    if mode not in valid:
        raise ValueError(f"unsupported topology mode: {mode}")
    # substrate-walled: eclipse / Sybil / peer-set capture / ledger-peer manipulation all require a
    # multi-peer (10-30 node) topology so a node can be surrounded by adversary peers. The current
    # substrate is a 2-node pair (each node has exactly one peer), so none of these faults can be
    # performed. We do NOT fabricate observation_overrides (no fake peer edges / quorum / tips / stake
    # distributions) and we do NOT return a pass. Honest not_applicable; real implementation pending a
    # multi-peer substrate (phase 2).
    result = {
        "target_node": str(config.get("target_node", "")),
        "mode": mode,
        "status": "not_applicable",
        "measured": False,
        "reason": "substrate-walled: eclipse/Sybil/peer-set/ledger-peer manipulation requires a "
                  "multi-peer (10-30 node) topology; current substrate is 2-node. Real implementation "
                  "pending a multi-peer substrate (phase 2). Not faking a pass.",
    }
    # pass existing overrides through unchanged; write NO fabricated topology/quorum/tip overrides
    overrides = dict(metadata.get("observation_overrides") or {})
    metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}

def run_topology_fault(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    updated = apply_topology_mode(metadata=metadata, mode=mode, config=config)
    metadata["observation_overrides"] = updated["observation_overrides"]
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": mode,
        "target_node": str(config.get("target_node", "")),
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": updated["result"],
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "simulate_peer_set_capture",
            "inject_hot_warm_churn",
            "perturb_ledger_peer_weights",
            "substitute_big_ledger_peers",
        ],
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_topology_fault(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
