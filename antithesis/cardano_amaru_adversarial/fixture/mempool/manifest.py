"""Write mempool_corpus.json from the signed txs build.sh + edits.py just produced.

Expected verdicts are the rule each case is DESIGNED to hit; the nodes' responses are graded by
workload/mempool_differential.py. The parity token of a size reject is the ORIGINAL ledger size
(16385), which both nodes print; a node measuring a re-encoding would print the smaller canonical
size (recorded per case) or accept.
"""
import hashlib, json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZES = json.loads((HERE / "sizes.json").read_text())
NOTES = {"canonical": "canonical encoding (reference)",
         "body": "tx body with the fee as a 9-byte uint (+4 bytes over canonical)",
         "wits": "vkey witness array as indefinite length (+1 byte)",
         "aux": "aux: indefinite metadata map + 8 non-minimal text heads (+9 bytes)"}

cases = []
for name, s in SIZES.items():
    variant = name.split("-")[1]
    reject = name.endswith("16385")
    case = {"case_id": name, "tx_file": f"{name}.tx", "expected": "reject" if reject else "accept",
            "rule": "UTXO MaxTxSizeUTxO (original bytes, IsValid excluded)" if reject else "control",
            "reason_classes": ["tx_too_large"] if reject else [],
            "note": f"{NOTES[variant]}; original ledger size {s['original_ledger_size']}, canonical "
                    f"re-encoding {s['canonical_ledger_size']}", **s}
    if reject:
        case["credential"] = str(s["original_ledger_size"])
    cases.append(case)
cases.append({"case_id": "mp-base", "tx_file": "mp-base.tx", "expected": "accept", "rule": "control",
              "reason_classes": [], "note": "small valid tx; also the duplicate-resubmission case"})
for case in cases:
    cbor = bytes.fromhex(json.loads((HERE / case["tx_file"]).read_text())["cborHex"])
    case.update({"input": os.environ["IN"], "cbor_size": len(cbor),
                 "cbor_sha256": hashlib.sha256(cbor).hexdigest()})

(HERE / "mempool_corpus.json").write_text(json.dumps({
    "description": "Mempool / submit-path phase-1 differential family (cardano-node vs Amaru): "
    "max-tx-size exact boundary in canonical and non-canonical encodings, duplicate resubmission, "
    "HTTP-level robustness with a liveness oracle. Every tx spends the committed funding UTxO; fee "
    "1 ADA. Violations idempotent; accepts single-use. See "
    "dwarf/docs/mempool-submit-path-differential-family.md.",
    "max_tx_size": 16384, "cases": cases}, indent=1) + "\n")
print(f"wrote {len(cases)} cases")
