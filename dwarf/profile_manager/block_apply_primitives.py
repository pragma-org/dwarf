"""DWARF block-apply bridge primitives (PATH-B forged-block pipeline).

Formalizes the block-apply adversary bridge used to demonstrate ledger findings at block-apply
(vs submit) against amaru: forge a block at amaru tip+1 under amaru's own (buggy) epoch nonce,
serve it over one socket, repoint amaru, and observe adopt / block-fetch / apply / crash.

Thin wrappers over the tracked helper scripts in dwarf/block_apply/. The forced-nonce VRF+KES forge
itself requires cardano-crypto tooling (cod-forge) and the serve requires the tx-submission responder
(wire); these primitives invoke the tracked scripts and record their outputs into the run bundle.
Kept in a separate module so a wrapper error can never break profile_manager.primitives.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from profile_manager.primitives import LoadPrimitive, AssertionPrimitive

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


class _ScriptLoadPrimitive(LoadPrimitive):
    """Base: invoke a tracked block_apply script, record result; graceful if deps absent."""
    primitive_name = "block_apply_base"
    script = None  # relative to dwarf/block_apply/

    def run(self, handle, rng):
        out = _run_dir(handle) / self.params.get("output_dir", f"outputs/{self.primitive_name}")
        out.mkdir(parents=True, exist_ok=True)
        result = {"primitive": self.primitive_name, "params": dict(self.params)}
        script = _SCRIPTS / self.script if self.script else None
        if script and script.exists():
            args = [str(script)] + [str(a) for a in self.params.get("args", [])]
            if script.suffix == ".py":
                args = ["python3"] + args
            elif script.suffix == ".sh":
                args = ["bash"] + args
            result["exec"] = _sh(args)
            result["outcome"] = "ok" if result["exec"].get("rc") == 0 else "error"
        else:
            # external dependency (cod-forge forge / wire serve) or script missing: record intent
            result["outcome"] = "deferred"
            result["note"] = f"tracked script {self.script} not present or external-dep; see dwarf/block_apply/README.md"
        (out / "result.json").write_text(json.dumps(result, indent=2))
        return result


class BuildBlockSegments(_ScriptLoadPrimitive):
    """Build Conway block body segments [[tx_body],[tx_wits],aux,invalid] from a tx fixture."""
    primitive_name = "build_block_segments"
    script = "build_block_segments.py"


class ForgeBlock(_ScriptLoadPrimitive):
    """Forge a block header at amaru tip+1 with a forced epoch nonce (VRF+KES). External: cod-forge."""
    primitive_name = "forge_block"
    script = None  # forced-nonce VRF+KES forge is external (cod-forge cardano-crypto tool)


class AssembleBlockjson(_ScriptLoadPrimitive):
    """Assemble block.json (header+body, point_hash) with the race-guard body_hash check."""
    primitive_name = "assemble_blockjson"
    script = "assemble_blockjson.py"


class ServeCraftedBlock(_ScriptLoadPrimitive):
    """Serve a crafted block to amaru over one socket (chain-sync RollForward + block-fetch). External: wire."""
    primitive_name = "serve_crafted_block"
    script = None  # tx-submission/block-fetch responder is external (wire runtime_serve_crafted_block)


class BlockApplyDifferential(_ScriptLoadPrimitive):
    """Reset+repoint amaru to the serve, watch adopt/block-fetch/apply/crash, record the verdict."""
    primitive_name = "block_apply_differential"
    script = "block_apply_differential.sh"


class BlockApplyOutcomeMatches(AssertionPrimitive):
    """Pass iff the block_apply_differential outcome matches the scenario's expected outcome
    (one of: crash | accept_invalid | adopt | reject)."""

    def evaluate(self, handle):
        expected = str(self.params.get("expected_outcome") or "accept_invalid")
        rd = _run_dir(handle)
        observed = None
        for p in rd.rglob("block_apply_differential/result.json"):
            try:
                observed = json.loads(p.read_text()).get("verdict") or json.loads(p.read_text()).get("outcome")
            except Exception:  # noqa: BLE001
                pass
        ok = observed is not None and observed == expected
        return {
            "primitive": "block_apply_outcome_matches",
            "params": dict(self.params),
            "evaluated_value": {"expected": expected, "observed": observed},
            "result": "pass" if ok else "fail",
            "note": None if ok else f"expected block-apply outcome {expected!r}, observed {observed!r}",
        }
