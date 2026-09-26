"""Write collateral_corpus.json from the signed txs build.sh just produced.

Expected verdicts and target reason classes are the ledger rule each case is DESIGNED to hit;
the nodes' actual responses are graded by workload/collateral_differential.py, never here.
"""
import hashlib, json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = os.environ
FOREIGN = E["F1_KEYHASH"]

# case_id: (expected, reason_classes, credential, rule, note)
CASES = {
    "no-collateral": ("reject", ["no_collateral"], None,
        "UTXO NoCollateralInputs", "Plutus V3 mint with a redeemer and no collateral inputs"),
    "insufficient-at-boundary": ("reject", ["insufficient_collateral"], None,
        "UTXO InsufficientCollateral",
        "fee 400001 needs ceil(1.5*fee) = 600002; the return leaves 600001 (declared = effective)"),
    "total-collateral-mismatch": ("reject", ["total_collateral_mismatch"], None,
        "UTXO IncorrectTotalCollateralField", "effective 700000, declared 699999"),
    "negative-asset-return": ("reject", ["collateral_non_ada"], None,
        "UTXO CollateralContainsNonADA",
        "the collateral return carries 1 unit of an asset absent from the collateral inputs, so "
        "the collateral balance has a NEGATIVE asset quantity"),
    "return-too-small": ("reject", ["output_too_small"], None,
        "UTXO BabbageOutputTooSmallUTxO (collateral return)", "collateral return of 100 lovelace"),
    "return-wrong-network": ("reject", ["wrong_network"], None,
        "UTXO WrongNetwork (collateral return)", "collateral return paid to a mainnet address"),
    "too-many-collateral": ("reject", ["too_many_collateral", "missing_witness"], None,
        "UTXO TooManyCollateralInputs and UTXOW MissingVKeyWitness (co-occurring)",
        "4 collateral inputs (max 3): ours + 3 genesis UTxOs whose keys never sign; two rules "
        "apply, so the reported reason is a PRECEDENCE observation"),
    "foreign-collateral-redeemer": ("reject", ["missing_witness"], FOREIGN,
        "UTXOW MissingVKeyWitness (collateral input key)",
        "the only collateral is a genesis UTxO whose key never signs; the tx has a redeemer"),
    "exunits-too-big": ("reject", ["exunits_too_big"], None,
        "UTXO ExUnitsTooBigUTxO", "redeemer memory 14000001 > maxTxExUnits 14000000; fee 2000001"),
    "integrity-hash-mismatch": ("reject", ["integrity_hash"], None,
        "UTXOW PPViewHashesDontMatch", "script-integrity hash computed from an altered PlutusV3 cost model"),
    "missing-redeemer": ("reject", ["missing_redeemer"], None,
        "UTXOS CollectErrors NoRedeemer / UTXOW MissingRedeemers",
        "the Plutus mint has no redeemer; the integrity hash is the one the ledger expects for an "
        "empty redeemer map, so only the missing redeemer is wrong"),
    "extra-redeemer": ("reject", ["extra_redeemer"], None,
        "UTXOW ExtraRedeemers",
        "a spend redeemer for the key-locked input next to the valid mint redeemer; hash recomputed"),
    "valid-minimal": ("accept", [], None, "control", "collateral = the funding input, no return"),
    "valid-with-return": ("accept", [], None, "control",
        "collateral return + declared total_collateral 700000 (correct)"),
    "valid-at-boundary": ("accept", [], None, "control (over-strictness)",
        "effective collateral exactly ceil(1.5*fee) = 600002"),
    "p1-foreign-collateral-no-redeemers": ("reject", ["missing_witness"], FOREIGN,
        "UTXOW MissingVKeyWitness (collateral input key, no redeemers)",
        "PREDICTED DIVERGENCE: no scripts; a genesis UTxO whose key never signs is named as "
        "collateral. The ledger's witness set is inputs + collateral regardless of redeemers; "
        "Amaru (collateral.rs) skips the witness when there are no redeemers. Single-use."),
    "p1-control-no-collateral": ("accept", [], None, "control (P1 counter-control)",
        "P1 without the collateral field: a plain signed spend both nodes must accept"),
    "p2-total-mismatch-no-redeemers": ("accept", [], None, "control (over-strictness)",
        "no scripts; declared total_collateral disagrees with the return: the collateral "
        "balance checks apply only with redeemers"),
}

cases = []
for cid, (expected, classes, cred, rule, note) in CASES.items():
    cbor = bytes.fromhex(json.loads((HERE / f"{cid}.tx").read_text())["cborHex"])
    case = {"case_id": cid, "tx_file": f"{cid}.tx", "expected": expected, "input": E["IN"],
            "cbor_size": len(cbor), "cbor_sha256": hashlib.sha256(cbor).hexdigest(),
            "rule": rule, "reason_classes": classes, "note": note,
            "single_use": expected == "accept" or cid.startswith("p1-")}
    if cred:
        case["credential"] = cred
    cases.append(case)

(HERE / "collateral_corpus.json").write_text(json.dumps({
    "description": "Collateral / redeemer phase-1 differential family (cardano-node vs Amaru). "
    "Every case spends the committed funding UTxO and mints under a PlutusV3 always-succeeds "
    f"policy ({E['POL']}); fee 400001 >> min, no validity interval, real script-integrity hash "
    "from pparams.json. Violations idempotent; single_use cases need a clean mempool each.",
    "policy_id": E["POL"],
    "cases": cases,
}, indent=2) + "\n")
print(f"wrote {len(cases)} cases")
