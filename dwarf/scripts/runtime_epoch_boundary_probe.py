from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Real amaru epoch-state observation.
# amaru emits, in its node log:
#   snapshot accounts=<n> dreps=<n> pools=<n> active_stake=<n> pools_voting_stake=<n> dreps_voting_stake=<n> epoch=<n>
#   pots.dump treasury=<n> reserves=<n> fees=<n> donations=<n>
# These give real stake-snapshot and reward/pot state. amaru does NOT expose a leadership
# schedule / epoch nonce in its log, and a boundary cannot be forced on this substrate
# (requires the next-epoch nonce / cross-epoch forge). Those return honest not-observable /
# not-applicable rather than fabricated constants.

_SNAPSHOT_RE = re.compile(
    r"snapshot accounts=(\d+) dreps=(\d+) pools=(\d+) active_stake=(\d+) "
    r"pools_voting_stake=(\d+) dreps_voting_stake=(\d+) epoch=(\d+)"
)
_POTS_RE = re.compile(r"pots\.dump treasury=(\d+) reserves=(\d+) fees=(\d+) donations=(\d+)")


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _nodes(metadata: dict) -> list:
    return list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])


def _resolve_amaru_log(metadata: dict, config: dict) -> tuple[str | None, str | None]:
    """Return (log_path, node_id) for the amaru target, or (None, reason)."""
    target = str(config.get("target_node") or "")
    nodes = _nodes(metadata)
    chosen = None
    for n in nodes:
        nid = str(n.get("id") or n.get("name") or "")
        impl = str(n.get("impl") or n.get("implementation") or "")
        if target and nid == target:
            chosen = n
            break
        if not target and impl == "amaru":
            chosen = n
            break
    if chosen is None:
        # explicit override, else the known live pair log
        override = config.get("amaru_log")
        if override:
            return str(override), None
        return None, "no amaru node resolvable in runtime metadata"
    log_path = chosen.get("log_path") or config.get("amaru_log")
    return (str(log_path) if log_path else None), str(chosen.get("id") or chosen.get("name") or "")


def _read_log(log_path: str | None, config: dict) -> str | None:
    for candidate in (log_path, config.get("amaru_log"), "/tmp/amaru-pair1.log"):
        if candidate and Path(candidate).exists():
            try:
                return Path(candidate).read_text(errors="replace")
            except OSError:
                continue
    return None


def _library_fallback(mode: str) -> dict:
    return {
        "measured": False,
        "mode_scope": "library-fallback",
        "note": f"{mode}: no runtime metadata (library mode) — no real observation performed",
    }


def apply_epoch_boundary_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    overrides = dict(metadata.get("observation_overrides") or {})
    log_path, node_id = _resolve_amaru_log(metadata, config)
    log_text = _read_log(log_path, config)

    if mode == "force_epoch_boundary":
        # Honestly not performable on this substrate: forcing an epoch boundary requires the
        # next-epoch active nonce (cross-epoch forge) which this single-pool within-epoch
        # substrate cannot produce. Do not fabricate a boundary.
        section = {
            "measured": False,
            "applicable": False,
            "status": "not-applicable-substrate-walled",
            "reason": "cannot force an epoch boundary on this substrate (requires the next-epoch active nonce / cross-epoch forge)",
            "target_node": node_id,
        }
        result = {"epoch_boundary": section}

    elif mode == "simulate_stake_snapshot_update":
        if log_text is None:
            section = {"measured": False, "status": "not-observable", "reason": "amaru log not readable", "target_node": node_id}
        else:
            snaps = [
                {
                    "accounts": int(m[0]), "dreps": int(m[1]), "pools": int(m[2]),
                    "active_stake": int(m[3]), "pools_voting_stake": int(m[4]),
                    "dreps_voting_stake": int(m[5]), "epoch": int(m[6]),
                }
                for m in _SNAPSHOT_RE.findall(log_text)
            ]
            if not snaps:
                section = {"measured": False, "status": "not-observable", "reason": "no stake-snapshot lines in amaru log", "target_node": node_id}
            else:
                # Freeze-window stability: within each epoch, every snapshot line must be identical
                # (a frozen snapshot must not mutate mid-epoch).
                by_epoch: dict[int, list] = {}
                for s in snaps:
                    by_epoch.setdefault(s["epoch"], []).append(s)
                stable = all(all(x == v[0] for x in v) for v in by_epoch.values())
                section = {
                    "measured": True,
                    "target_node": node_id,
                    "epochs_observed": sorted(by_epoch),
                    "snapshots": snaps[-5:],
                    "freeze_window_stable": stable,
                }
        result = {"stake_snapshot": section}

    elif mode == "trigger_rupd_pulse":
        if log_text is None:
            section = {"measured": False, "status": "not-observable", "reason": "amaru log not readable", "target_node": node_id}
        else:
            pots = _POTS_RE.findall(log_text)
            if not pots:
                section = {"measured": False, "status": "not-observable", "reason": "no pots.dump lines in amaru log", "target_node": node_id}
            else:
                t, r, f, d = pots[-1]
                section = {
                    "measured": True,
                    "target_node": node_id,
                    "pots": {"treasury": int(t), "reserves": int(r), "fees": int(f), "donations": int(d)},
                    "pots_samples": len(pots),
                    "note": "real reward/pot state from amaru pots.dump; rupd pulse not separately instrumented by amaru",
                }
        result = {"reward_calculation": section}

    elif mode == "recompute_leadership_schedule":
        # amaru does not expose a leadership schedule / epoch nonce in its log — honestly not observable.
        section = {
            "measured": False,
            "status": "not-observable",
            "reason": "amaru does not expose a leadership schedule or epoch nonce in its log/endpoints",
            "target_node": node_id,
        }
        result = {"leadership_schedule": section}

    else:
        raise ValueError(f"unsupported epoch-boundary mode: {mode}")

    # Record only real observed state; do not fabricate observation_overrides.
    return {"result": result, "observation_overrides": overrides}


def run_epoch_boundary_probe(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    updated = apply_epoch_boundary_mode(metadata=metadata, mode=mode, config=config)
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
            "force_epoch_boundary",
            "simulate_stake_snapshot_update",
            "recompute_leadership_schedule",
            "trigger_rupd_pulse",
        ],
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if "runtime_metadata_path" not in config:
        # library mode: labeled fallback, no fabricated success
        out = Path(config.get("output_dir", "."))
        out.mkdir(parents=True, exist_ok=True)
        report = {"mode": args.mode, "result": {args.mode: _library_fallback(args.mode)}}
        (out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"mode={args.mode} library-fallback", flush=True)
        return 0
    report = run_epoch_boundary_probe(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
