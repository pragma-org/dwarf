"""Write metadata_corpus.json from the signed txs build.sh + edits.py just produced.

Expected verdicts, reason classes and the parity token are the rule each case is DESIGNED to hit;
the nodes' actual responses are graded by workload/metadata_differential.py, never here. For the
hash rules the parity token is the hash the LEDGER must compute: blake2b-256 of the auxiliary-data
bytes as they sit in the tx. A node that hashed a re-encoding would print a different value.
"""
import hashlib, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import txedit  # noqa: E402

E = os.environ


def sent_aux_hash(cid: str) -> str:
    _, _, tail = txedit.split_tx(txedit.load(HERE, f"{cid}.tx"))
    assert tail[:1] == b"\xf5" and tail[1:] != b"\xf6", cid  # isValid, then non-null aux
    return hashlib.blake2b(tail[1:], digest_size=32).hexdigest()


CANON = sent_aux_hash("canonical-valid")
SIZE_NOTE = ("cardano-node 11.1.2 enforces the 64-byte limit in its DECODER (text/bytes .size (0..64)); "
             "older decoders (cardano-submit-api 10.7.1, cardano-cli 10.16) accept the bytes and left it "
             "to the UTXOW InvalidMetadata rule. Through a 10.7.1 submit-api the node closes the local "
             "connection (BearerClosed) and the case is INCONCLUSIVE")

# case_id: (expected, reason_classes, parity token, rule, note)
CASES = {
    "noncanon-uint-hash-canonical": ("reject", ["conflicting_hash"], sent_aux_hash("noncanon-uint-hash-canonical"),
        "UTXOW ConflictingMetadataHash (hash over the SENT bytes)",
        "label 674 as a 5-byte uint; body hash over the CANONICAL re-encoding. The token is the hash "
        "of the sent bytes: a node hashing its own re-encoding would accept instead"),
    "noncanon-indef-hash-canonical": ("reject", ["conflicting_hash"], sent_aux_hash("noncanon-indef-hash-canonical"),
        "UTXOW ConflictingMetadataHash (hash over the SENT bytes)",
        "indefinite-length map + list; body hash over the canonical re-encoding"),
    "hash-mismatch": ("reject", ["conflicting_hash"], CANON, "UTXOW ConflictingMetadataHash",
        "body hash of unrelated bytes"),
    "hash-missing": ("reject", ["missing_hash"], CANON, "UTXOW MissingTxBodyMetadataHash",
        "aux present, body auxiliary_data_hash absent"),
    "aux-missing": ("reject", ["missing_aux"], CANON, "UTXOW MissingTxMetadata",
        "body auxiliary_data_hash present, aux data null"),
    "text-65": ("decode_reject", [], None, "CDDL metadatum text .size (0..64)", "65-byte text. " + SIZE_NOTE),
    "bytes-65": ("decode_reject", [], None, "CDDL metadatum bytes .size (0..64)", "65-byte bytes. " + SIZE_NOTE),
    "nested-text-65": ("decode_reject", [], None, "CDDL metadatum text .size (0..64)",
        "65-byte text nested in a list inside a map. " + SIZE_NOTE),
    "bignum-small": ("decode_reject", [], None, "CDDL metadatum int (no tag-2 bignum)",
        "int 5 encoded as a tag-2 bignum"),
    "int-overflow": ("decode_reject", [], None, "CDDL metadatum int range", "int 2^64 (tag-2 bignum)"),
    "dup-labels": ("decode_reject", [], None, "CDDL metadata map (no duplicate labels)", "label 674 twice"),
    # controls (expected accept; single-use)
    "noncanon-uint-hash-sent": ("accept", [], None, "control (hash over the sent bytes)",
        "label 674 as a 5-byte uint; body hash over the SENT bytes"),
    "noncanon-indef-hash-sent": ("accept", [], None, "control (hash over the sent bytes)",
        "indefinite-length map + list; body hash over the sent bytes"),
    "canonical-valid": ("accept", [], None, "control (encoder anchor)", "canonical aux, hash over it"),
    "text-64-valid": ("accept", [], None, "control (size boundary)", "64-byte text"),
    "bytes-64-valid": ("accept", [], None, "control (size boundary)", "64-byte bytes"),
    "indef-text": ("accept", [], None, "control (encoding)", "text as an indefinite-length chunked string"),
}

cases = []
for cid, (expected, classes, token, rule, note) in CASES.items():
    cbor = bytes.fromhex(json.loads((HERE / f"{cid}.tx").read_text())["cborHex"])
    case = {"case_id": cid, "tx_file": f"{cid}.tx", "expected": expected, "input": E["IN"],
            "cbor_size": len(cbor), "cbor_sha256": hashlib.sha256(cbor).hexdigest(),
            "rule": rule, "reason_classes": classes, "note": note}
    if token:
        case["credential"] = token
    cases.append(case)

(HERE / "metadata_corpus.json").write_text(json.dumps({
    "description": "Transaction-metadata / auxiliary-data-hash phase-1 differential family "
    "(cardano-node vs Amaru). Every case spends the committed funding UTxO (fee 300000 >> min, no "
    "validity interval) and is derived from the cardano-cli md-base.tx by edits.py (raw aux bytes, "
    "re-signed body). Violations and decode edges idempotent; controls single-use. See "
    "dwarf/docs/metadata-phase1-differential-family.md.",
    "canonical_aux_hash": CANON, "cases": cases}, indent=1) + "\n")
print(f"wrote {len(cases)} cases")
