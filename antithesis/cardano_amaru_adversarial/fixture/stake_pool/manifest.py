"""Write stake_pool_corpus.json from the signed txs build.sh just produced.

Expected verdicts and target reason classes are the ledger rule each case is DESIGNED to hit;
the nodes' actual responses are graded by workload/stake_pool_differential.py, never here.
"""
import hashlib, json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = os.environ
STAKE, OWNER, POOL, GEN = E["STAKE_HASH"], E["OWNER_HASH"], E["POOLID"], E["GEN_DELEG"]

# case_id: (expected, reason_classes, credential, rule, note)
CASES = {
    "stakereg-missing-witness": ("reject", ["missing_witness"], STAKE,
        "UTXOW MissingVKeyWitness (stake credential)", "Conway reg cert (tag 7), stake witness omitted"),
    "stakereg-wrong-key": ("reject", ["missing_witness"], STAKE,
        "UTXOW MissingVKeyWitness (stake credential)", "Conway reg cert signed by an unrelated stake key"),
    "stakereg-bad-deposit": ("reject", ["incorrect_deposit"], None,
        "DELEG IncorrectDeposit", "Conway reg cert declaring 1 ADA vs pparam 2 ADA, tx balanced to the "
        "DECLARED 1 ADA; witnessed. cardano-node also reports ValueNotConservedUTxO (it balances "
        "against the pparam deposit); Amaru reports only the deposit rule"),
    "stakereg-bad-deposit-balanced": ("reject", ["incorrect_deposit"], None,
        "DELEG IncorrectDeposit", "same 1 ADA cert, tx balanced to the PPARAM 2 ADA so value is "
        "conserved and only the declared-deposit rule applies; witnessed"),
    "regvote-missing-witness": ("reject", ["missing_witness"], STAKE,
        "UTXOW MissingVKeyWitness (stake credential)", "reg + vote-deleg to AlwaysAbstain (tag 12), stake witness omitted"),
    "regdeleg-missing-witness": ("reject", ["missing_witness"], STAKE,
        "UTXOW MissingVKeyWitness (stake credential)", "reg + stake-deleg to a registered genesis pool (tag 11), stake witness omitted"),
    "regdeleg-unknown-pool": ("reject", ["unknown_pool"], None,
        "DELEG DelegateeStakePoolNotRegistered", "reg + stake-deleg to an unregistered pool; witnessed"),
    "poolreg-missing-cold": ("reject", ["missing_witness"], POOL,
        "UTXOW MissingVKeyWitness (pool cold key)", "pool registration, cold-key witness omitted (owner signs)"),
    "poolreg-missing-owner": ("reject", ["missing_witness"], OWNER,
        "UTXOW MissingVKeyWitness (pool owner)", "pool registration, owner witness omitted (cold signs)"),
    "poolreg-cost-too-low": ("reject", ["pool_cost_too_low"], None,
        "POOL StakePoolCostTooLow", "pool cost minPoolCost-1; cold + owner witnessed"),
    "poolreg-wrong-network": ("reject", ["wrong_network"], None,
        "POOL WrongNetwork", "pool reward account on Mainnet; cold + owner witnessed"),
    "wdrl-unregistered": ("reject", ["wdrl_not_registered"], None,
        "CERTS WithdrawalsNotInRewards", "withdraw 0 from a never-registered account; witnessed"),
    "wdrl-genesis-no-witness": ("reject", ["missing_witness", "wdrl_not_drep_delegated"], GEN,
        "UTXOW MissingVKeyWitness and/or pv10 ConwayWdrlNotDelegatedToDRep (co-occurring)",
        "withdraw 0 from a genesis-registered delegator (no key, no DRep delegation): two rules "
        "apply, so the reported reason is a PRECEDENCE observation"),
    "stakereg-present": ("accept", [], None, "control", "Conway reg cert with stake witness"),
    "stakereg-legacy-no-witness": ("accept", [], None, "control (over-strictness)",
        "legacy Shelley reg cert (tag 0) with NO stake witness: the ledger requires none"),
    "regvote-present": ("accept", [], None, "control", "reg + vote-deleg AlwaysAbstain, stake witness"),
    "poolreg-present": ("accept", [], None, "control", "pool registration, cold + owner witnessed"),
}

cases = []
for cid, (expected, classes, cred, rule, note) in CASES.items():
    cbor = bytes.fromhex(json.loads((HERE / f"{cid}.tx").read_text())["cborHex"])
    case = {"case_id": cid, "tx_file": f"{cid}.tx", "expected": expected, "input": E["IN"],
            "cbor_size": len(cbor), "cbor_sha256": hashlib.sha256(cbor).hexdigest(),
            "rule": rule, "reason_classes": classes, "note": note}
    if cred:
        case["credential"] = cred
    cases.append(case)

(HERE / "stake_pool_corpus.json").write_text(json.dumps({
    "description": "Conway stake/pool certificate + reward-withdrawal phase-1 differential family "
    "(cardano-node vs Amaru). Fresh credentials only (not-registered is the correct pre-state), so "
    "each violation hits exactly the targeted rule. All cases spend the committed funding UTxO; "
    "fee 300000 >> min, no validity interval. Violations idempotent; controls single-use. See "
    "dwarf/docs/stake-pool-withdrawal-phase1-differential-family.md.",
    "cases": cases}, indent=1) + "\n")
print(f"wrote {len(cases)} cases")
