"""DWARF block-apply bridge primitives.

Demonstrate ledger behaviour at BLOCK APPLY (not submit) on a self-composed devnet: a crafted
transaction is put in a block forged from the devnet producer's own leader header (same slot,
VRF and opcert; body commitment and KES signature redone with the pool's own KES key), served
through the mux proxy to the Amaru target and to an isolated cardano-node reference, and each
node's own verdict on that block is recorded.

Live path (``scripts.runtime_block_apply_bridge``), selected by the step parameters:

* build_block_segments ``recipe``        -> craft the transaction(s) live (setup submitted honestly);
                                          isvalid-false-donation | gov-scriptcontext
* forge_block ``reference_node``         -> forge plan: producer KES key proven against its opcert
* serve_crafted_block (after a plan)     -> proxy + Amaru repoint + reference consumer, then arm
* block_apply_differential (after serve) -> per-node verdicts (+ epoch-aligned pot snapshots)
* invalid_tx_body_differential / epoch_boundary_pot_differential -> verdicts from those snapshots

Scenarios that still name the earlier campaign scripts (``tx_file`` fixtures, ``args``) keep the
legacy script invocation unchanged. Kept in a separate module so a wrapper error can never break
profile_manager.primitives.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from profile_manager.primitives import AssertionPrimitive, LoadPrimitive

# dwarf/ root relative to this file (dwarf/profile_manager/block_apply_primitives.py -> dwarf/)
_DWARF = Path(__file__).resolve().parents[1]
_SCRIPTS = _DWARF / "block_apply"


def _run_dir(handle) -> Path:
    rd = getattr(handle, "run_dir", None)
    return Path(rd) if rd else Path.cwd()


def _sh(args, cwd=None, timeout=240):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return {"rc": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-4000:]}
    except Exception as exc:  # noqa: BLE001
        return {"rc": None, "error": f"{type(exc).__name__}: {exc}"}


def _bridge():
    from scripts import runtime_block_apply_bridge  # noqa: PLC0415

    return runtime_block_apply_bridge


def _runtime_path(rd: Path, params: dict) -> Path:
    path = Path(str(params.get("runtime_metadata_path") or "outputs/substrate-compose/runtime.json"))
    return path if path.is_absolute() else rd / path


def _write(out: Path, result: dict) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return result


class _ScriptLoadPrimitive(LoadPrimitive):
    """Base: invoke a tracked block_apply script, record result; graceful if deps absent."""
    primitive_name = "block_apply_base"
    script = None  # relative to dwarf/block_apply/

    def _out(self, handle) -> Path:
        return _run_dir(handle) / self.params.get("output_dir", f"outputs/{self.primitive_name}")

    def run(self, handle, rng):
        return self._legacy(handle)

    def _legacy(self, handle):
        out = self._out(handle)
        out.mkdir(parents=True, exist_ok=True)
        result = {"primitive": self.primitive_name, "params": dict(self.params)}
        script = _SCRIPTS / self.script if self.script else None
        if script and script.exists():
            args = [str(script)] + [str(a) for a in self.params.get("args", [])]
            if script.suffix == ".py":
                args = [sys.executable or "python3"] + args
            elif script.suffix == ".sh":
                args = ["bash"] + args
            result["exec"] = _sh(args)
            result["outcome"] = "ok" if result["exec"].get("rc") == 0 else "error"
        else:
            result["outcome"] = "deferred"
            result["note"] = (f"no live bridge state for {self.primitive_name} and tracked script {self.script} "
                              "is not present; see dwarf/block_apply/README.md")
        (out / "result.json").write_text(json.dumps(result, indent=2))
        return result


class BuildBlockSegments(_ScriptLoadPrimitive):
    """Craft the block's transaction(s). ``recipe`` builds them live on the devnet."""
    primitive_name = "build_block_segments"
    script = "build_block_segments.py"

    def run(self, handle, rng):
        recipe = self.params.get("recipe")
        if not recipe:
            return self._legacy(handle)
        bridge = _bridge()
        rd = _run_dir(handle)
        if recipe == bridge.DONATION_RECIPE:
            runtime = json.loads(_runtime_path(rd, self.params).read_text(encoding="utf-8"))
            record = bridge.craft_isvalid_false_donation(
                runtime=runtime, out_dir=self._out(handle), reference_node=str(self.params["reference_node"]),
                donation=int(self.params.get("donation_lovelace", 5_000_000)),
            )
        elif recipe == bridge.GOV_SCRIPTCONTEXT_RECIPE:
            runtime = json.loads(_runtime_path(rd, self.params).read_text(encoding="utf-8"))
            record = bridge.craft_gov_scriptcontext(
                runtime=runtime, out_dir=self._out(handle), reference_node=str(self.params["reference_node"]),
                target_node=str(self.params.get("target_node", "node1")),
            )
        else:
            raise ValueError(f"unknown block-apply recipe {recipe!r}")
        state = bridge.load_state(rd)
        state["craft"] = record
        bridge.save_state(rd, state)
        return _write(self._out(handle), {"primitive": self.primitive_name, "params": dict(self.params),
                                          "outcome": "ok", **record})


class ForgeBlock(_ScriptLoadPrimitive):
    """Write the forge plan: the producer pool's own KES key (proven against its opcert)."""
    primitive_name = "forge_block"
    script = None

    def run(self, handle, rng):
        rd = _run_dir(handle)
        bridge = _bridge()
        state = bridge.load_state(rd)
        if "craft" not in state or not self.params.get("reference_node"):
            return self._legacy(handle)
        runtime = json.loads(_runtime_path(rd, self.params).read_text(encoding="utf-8"))
        plan = bridge.write_forge_plan(runtime=runtime, out_dir=bridge.bridge_dir(rd),
                                       producer_node=str(self.params["reference_node"]),
                                       transaction_files=state["craft"]["transaction_files"])
        state["plan"] = plan
        bridge.save_state(rd, state)
        public = {k: v for k, v in plan.items() if k != "kes_skey"}
        return _write(self._out(handle), {"primitive": self.primitive_name, "params": dict(self.params),
                                          "outcome": "ok", "plan": public})


class AssembleBlockjson(_ScriptLoadPrimitive):
    """Legacy: assemble block.json from an external forge result (the live bridge forges on the wire)."""
    primitive_name = "assemble_blockjson"
    script = "assemble_blockjson.py"


class ServeCraftedBlock(_ScriptLoadPrimitive):
    """Serve the forged block through the mux proxy to the Amaru target and a cardano-node reference."""
    primitive_name = "serve_crafted_block"
    script = None

    def run(self, handle, rng):
        rd = _run_dir(handle)
        bridge = _bridge()
        if "plan" not in bridge.load_state(rd):
            return self._legacy(handle)
        report = bridge.serve_and_arm(
            run_dir=rd, runtime_metadata_path=_runtime_path(rd, self.params),
            target_node=str(self.params.get("target_node", "node1")),
            reference_node=str(self.params.get("reference_node", "node2")),
            snapshots=bool(self.params.get("pot_snapshots", False)),
            sync_timeout=float(self.params.get("sync_timeout_seconds", 900)),
        )
        return _write(self._out(handle), {"primitive": self.primitive_name, "params": dict(self.params), **report})


class BlockApplyDifferential(_ScriptLoadPrimitive):
    """Each node's own verdict on the forged block (adopt / reject / crash), compared per node."""
    primitive_name = "block_apply_differential"
    script = "block_apply_differential.sh"

    def run(self, handle, rng):
        rd = _run_dir(handle)
        bridge = _bridge()
        if "serve" not in bridge.load_state(rd):
            return self._legacy(handle)
        report = bridge.observe(run_dir=rd, watch_seconds=float(self.params.get("watch_seconds", 180)),
                                snapshots=bool(self.params.get("pot_snapshots", False)))
        return _write(self._out(handle), {"primitive": self.primitive_name, "params": dict(self.params), **report})


def _bridge_snapshots(rd: Path) -> dict | None:
    state = _bridge().load_state(rd)
    return (state.get("observe") or {}).get("snapshots") if state.get("observe") else None


def _find_result(rd: Path, primitive: str, explicit: str | None = None) -> dict | None:
    """The newest result.json written by ``primitive`` (wherever its output_dir put it)."""
    if explicit:
        path = Path(explicit) if Path(explicit).is_absolute() else rd / explicit
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    found = []
    for path in rd.glob("outputs/**/result.json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(body, dict) and body.get("primitive") == primitive:
            found.append((path.stat().st_mtime, body))
    return max(found, key=lambda item: item[0])[1] if found else None


def _stdout_verdict(result: dict | None) -> str | None:
    if not result:
        return None
    stdout = (result.get("exec") or {}).get("stdout") or ""
    match = re.search(r'"verdict":\s*"([^"]+)"', stdout)
    return match.group(1) if match else result.get("verdict")


def _assertion(name, params, *, expected, observed, data_points, note):
    ok = observed is not None and observed == expected
    return {
        "primitive": name,
        "params": dict(params),
        "evaluated_value": {"expected": expected, "observed": observed},
        "data_points_used": data_points,
        "result": "pass" if ok else "fail",
        "note": None if ok else note,
    }


class BlockApplyOutcomeMatches(AssertionPrimitive):
    """Pass iff the block_apply_differential outcome matches the scenario's expected outcome
    (one of: crash | accept_invalid | adopt | reject)."""

    def evaluate(self, handle):
        expected = str(self.params.get("expected_outcome") or "accept_invalid")
        result = _find_result(_run_dir(handle), "block_apply_differential", self.params.get("source_path"))
        observed = (result or {}).get("verdict") or (result or {}).get("outcome")
        points = [{"target": (result or {}).get("target"), "reference": (result or {}).get("reference"),
                   "forge": (result or {}).get("forge")}] if result else []
        return _assertion("block_apply_outcome_matches", self.params, expected=expected, observed=observed,
                          data_points=points,
                          note=f"expected block-apply outcome {expected!r}, observed {observed!r}")


class InvalidTxBodyDifferential(_ScriptLoadPrimitive):
    """Apply an is_valid=false tx and classify whether its body (donation/output/mint/withdrawal)
    leaked on amaru vs cardano = value-creation/theft detector."""
    primitive_name = "invalid_tx_body_differential"
    script = "invalid_tx_body_differential.py"

    def run(self, handle, rng):
        rd = _run_dir(handle)
        state = _bridge().load_state(rd)
        if "observe" not in state:
            return self._legacy(handle)
        effect = str(self.params.get("body_effect") or "donation")
        if effect != "donation":
            raise ValueError(f"the live bridge measures the donation body effect, not {effect!r}")
        snapshots = _bridge_snapshots(rd) or {}
        verdict = _bridge().value_creation_verdict(snapshots, donation=int(state["craft"]["donation"]))
        return _write(self._out(handle), {"primitive": self.primitive_name, "params": dict(self.params),
                                          "body_effect": effect, "block_apply": state["observe"].get("verdict"),
                                          "snapshots": snapshots, **verdict})


class InvalidTxBodyDropped(AssertionPrimitive):
    """Pass iff the is_valid=false tx body was DROPPED on amaru (only collateral consumed), i.e. no
    value creation/theft. Fails (= finding) if amaru leaked a body effect cardano dropped."""

    def evaluate(self, handle):
        expected = str(self.params.get("expected_verdict") or "no_body_leak")
        result = _find_result(_run_dir(handle), "invalid_tx_body_differential", self.params.get("source_path"))
        observed = _stdout_verdict(result)
        points = [{"evidence": (result or {}).get("evidence"), "reason": (result or {}).get("reason")}] if result else []
        return _assertion("invalid_tx_body_dropped", self.params, expected=expected, observed=observed,
                          data_points=points,
                          note=f"is_valid=false body-leak: expected {expected!r}, observed {observed!r}")


class EpochBoundaryPotDifferential(_ScriptLoadPrimitive):
    """Diff the pot TRANSITION (treasury/reserves/fees) across an epoch boundary: amaru's delta vs
    cardano-node's, from epoch-aligned snapshots on both sides of the boundary."""
    primitive_name = "epoch_boundary_pot_differential"
    script = "epoch_boundary_pot_differential.py"

    def run(self, handle, rng):
        rd = _run_dir(handle)
        if "observe" not in _bridge().load_state(rd):
            return self._legacy(handle)
        snapshots = _bridge_snapshots(rd) or {}
        result = {"primitive": self.primitive_name, "params": dict(self.params), "snapshots_aligned": bool(snapshots.get("aligned"))}
        if not snapshots.get("aligned"):
            result.update({"verdict": "inconclusive", "reason": snapshots.get("alignment_note") or "no snapshots"})
            return _write(self._out(handle), result)
        off_chain = _bridge().forged_chain_gate(snapshots)
        if off_chain:
            result.update({"verdict": "inconclusive", "measured": False, "reason": off_chain,
                           "forged_chain": snapshots.get("forged_chain")})
            return _write(self._out(handle), result)
        sys.path.insert(0, str(_SCRIPTS))
        from epoch_boundary_pot_differential import transition_diff  # noqa: PLC0415

        result.update(transition_diff(snapshots["amaru_before"], snapshots["amaru_after"],
                                      snapshots["cardano_before"], snapshots["cardano_after"],
                                      comparable=("treasury", "reserves")))
        # Run 20261109T231944Z-8cf541e9: a fees-only "divergence" with identical treasury deltas.
        result["not_compared"] = {"fees": (
            "Amaru's pots.dump fees is its STABLE-store pot, zeroed at each epoch start "
            "(reset_fees_and_donations); cardano-node's utxosFees is the tip's and keeps the closed epoch's "
            "fees until the reward update. Different quantities across a boundary; deltas recorded, not judged.")}
        result["epochs"] = snapshots.get("epochs")
        return _write(self._out(handle), result)


class EpochBoundaryPotsMatch(AssertionPrimitive):
    """Pass iff amaru's epoch-boundary pot transition matches cardano-node's (verdict CONFORMANT).
    Fails (= finding) on POT_TRANSITION_DIVERGENCE."""

    def evaluate(self, handle):
        expected = str(self.params.get("expected_verdict") or "CONFORMANT")
        result = _find_result(_run_dir(handle), "epoch_boundary_pot_differential", self.params.get("source_path"))
        observed = _stdout_verdict(result)
        points = [{"transition_diffs": (result or {}).get("transition_diffs"), "epochs": (result or {}).get("epochs"),
                   "reason": (result or {}).get("reason")}] if result else []
        return _assertion("epoch_boundary_pots_match", self.params, expected=expected, observed=observed,
                          data_points=points,
                          note=f"epoch-boundary pot transition: expected {expected!r}, observed {observed!r}")
