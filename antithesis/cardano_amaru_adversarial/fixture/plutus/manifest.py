"""Write plutus_corpus.json from the phase-2 txs plutus_build.sh just produced.

Expected verdicts are the phase-2 outcome each case is DESIGNED to produce (the tx is
phase-1-valid by construction); the nodes' actual behaviour is graded by
workload/plutus_differential.py, never here. ACCEPT cases consume the funding/collateral UTxO,
so they are single_use (run one per mempool reset); REJECT cases are idempotent.
"""
import hashlib, json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = os.environ
EXACT = f"({E.get('EXACT_STEPS','?')},{E.get('EXACT_MEM','?')}) steps,mem"

# case_id: (expected, single_use, note)
CASES = {
    "exu-exact": ("accept", True,
        f"is_valid=true, always-succeeds, declared ex-units = the EXACT cardano-computed cost "
        f"{EXACT}. KNIFE-EDGE: if amaru's computed cost differs by >=1, this is under-budget on "
        f"amaru -> amaru rejects while cardano accepts = is_valid VERDICT-DIVERGENCE."),
    "exu-under-steps": ("reject", False,
        "is_valid=true, always-succeeds, steps = exact-1 -> over budget -> script fails -> tag mismatch"),
    "exu-under-mem": ("reject", False,
        "is_valid=true, always-succeeds, mem = exact-1 -> over budget -> tag mismatch"),
    "exu-zero": ("reject", False,
        "is_valid=true, always-succeeds, ex-units (0,0) -> over budget -> tag mismatch"),
    "exu-ample": ("accept", True,
        "is_valid=true, always-succeeds, generous ex-units (<< maxTxExUnits) -> script succeeds -> accept"),
    "isvalid-false-succeeds": ("reject", False,
        "is_valid=FALSE but always-succeeds with ample budget -> script actually succeeds -> "
        "claimed-invalid contradicts reality -> tag mismatch (reject)"),
    "isvalid-false-underbudget": ("accept", True,
        "is_valid=FALSE, always-succeeds with (0,0) -> script fails on budget -> claim matches "
        "reality -> ACCEPT (collateral consumed)"),
    "alwaysfails-valid": ("reject", False,
        "is_valid=true, always-FAILS script -> script fails -> tag mismatch (reject)"),
    "alwaysfails-invalid": ("accept", True,
        "is_valid=FALSE, always-FAILS script -> claim matches reality -> ACCEPT (collateral consumed)"),
}

cases = []
for cid, (expected, single_use, note) in CASES.items():
    cbor = bytes.fromhex(json.loads((HERE / f"{cid}.tx").read_text())["cborHex"])
    cases.append({
        "case_id": cid, "tx_file": f"{cid}.tx", "expected": expected, "single_use": single_use,
        "cbor_size": len(cbor), "cbor_sha256": hashlib.sha256(cbor).hexdigest(), "note": note,
    })

(HERE / "plutus_corpus.json").write_text(json.dumps({
    "description": "Plutus phase-2 differential family (cardano-node 11.1.2 vs Amaru 0925). Each "
    "case mints under a PlutusV3 policy and is phase-1-valid, so any reject is phase-2 (is_valid "
    "tag vs actual VM result). Oracle: verdict parity (one-accepts-one-rejects = real is_valid "
    "divergence) + phase-2 reason-class parity; MASKED/unavailable = inconclusive. exu-exact is "
    f"built with the live-measured exact cost {EXACT}. See workload/plutus_differential.py.",
    "exact_cost": {"steps": E.get("EXACT_STEPS"), "mem": E.get("EXACT_MEM")},
    "cases": cases}, indent=1) + "\n")
print(f"wrote {len(cases)} cases; exact={EXACT}")
