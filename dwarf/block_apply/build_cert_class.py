#!/usr/bin/env python3
"""Build the 3 remaining missing-registration cert-class bodies at block-apply (companions to
cert-phantom's StakeDelegation). Each targets a NEVER-REGISTERED credential that amaru's bind_left
updates without a registration lookup (phantom-insert) -> accepts-invalid; cardano-node rejects
with a *NotRegistered* DELEG/GOV failure. Fresh credential key so the tx is otherwise well-witnessed
(isolates registration as the only divergence). is_valid=true, invalid=[].

Conway cert encodings:
  VoteDelegation       = [9,  stake_credential, drep]                (drep=[2] abstain -> only the
                                                                      unregistered STAKE cred is invalid)
  StakeVoteDelegation  = [10, stake_credential, pool_keyhash, drep]
  UpdateDRep           = [18, drep_credential, anchor/null]          (unregistered DREP cred)

Emits per type: <label>-block-segments.json (for cod-forge) + <label>-preflight.cbor (full Conway
tx for the submit pre-flight: [body, wits, true, null]).
"""
import json, cbor2, hashlib, sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

FIX = Path(__file__).resolve().parents[2] / "antithesis/cardano_amaru_adversarial/fixture"
sys.path.insert(0, str(FIX)); sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402
OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block")
POOL97B0 = bytes.fromhex("97b0f582dcf255b2c776f3540454b99fbf26e48833f275bfcd63bfcd")
ABSTAIN = [2]  # predefined drep target (always valid; isolates the unregistered delegator)


def raw_vkey(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def base_parts():
    seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
    pay = Ed25519PrivateKey.from_private_bytes(seed)
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b); tmpl_wits = cbor2.loads(w)
    inputs = base[0]; out0 = base[1][0]
    fund_addr = bytes(out0[0]); val = out0[1] if isinstance(out0[1], int) else out0[1][0]; fee = base[2]
    return pay, tmpl_wits, inputs, fund_addr, val, fee


def rewrap(orig, lst):
    return cbor2.CBORTag(orig.tag, lst) if isinstance(orig, cbor2.CBORTag) else lst


CASES = {
    "votedeleg":      {"type": 9,  "cred": "stake", "reject": "ConwayDelegFailure StakeKeyNotRegisteredDELEG (vote-deleg delegator unregistered)"},
    "stakevotedeleg": {"type": 10, "cred": "stake", "reject": "ConwayDelegFailure StakeKeyNotRegisteredDELEG (stake+vote-deleg delegator unregistered)"},
    "updatedrep":     {"type": 18, "cred": "drep",  "reject": "ConwayGovCertFailure DRepNotRegistered (update of unregistered DRep)"},
}


def build(label):
    spec = CASES[label]
    pay, tmpl_wits, inputs, fund_addr, val, fee = base_parts()
    cred_key = Ed25519PrivateKey.generate()
    cred_vk = raw_vkey(cred_key)
    cred_hash = hashlib.blake2b(cred_vk, digest_size=28).digest()
    credential = [0, cred_hash]  # key-hash credential
    t = spec["type"]
    if t == 9:
        cert = [9, credential, ABSTAIN]
    elif t == 10:
        cert = [10, credential, POOL97B0, ABSTAIN]
    elif t == 18:
        cert = [18, credential, None]
    body = {0: inputs, 1: [[fund_addr, val]], 2: fee, 4: [cert]}
    body_b = cbor2.dumps(body)
    h = hashlib.blake2b(body_b, digest_size=32).digest()
    vk_wits = [[raw_vkey(pay), pay.sign(h)], [cred_vk, cred_key.sign(h)]]
    wits = {0: rewrap(tmpl_wits[0], vk_wits)}
    segs = [[body], [wits], {}, []]
    seg_hex = cbor2.dumps(segs).hex()
    txid = hashlib.blake2b(body_b, digest_size=32).hexdigest()
    # full Conway tx for submit pre-flight: [body, wits, is_valid=true, aux=null]
    preflight_tx = cbor2.dumps([body, wits, True, None])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}-block-segments.json").write_text(json.dumps({
        "description": f"missing-registration cert class / {label}: cert type {t} on a NEVER-REGISTERED "
                       f"{spec['cred']} credential. amaru accepts (bind_left no reg lookup); cardano "
                       f"rejects {spec['reject']}.",
        "label": label, "cert_type": t, "cred_kind": spec["cred"],
        "body_segments_cbor_hex": seg_hex, "txid": txid,
        "cred_keyhash": cred_hash.hex(), "cert": [t] + (["cred", "pool97b0", "abstain"] if t == 10 else (["cred", "abstain"] if t == 9 else ["cred", "null"])),
        "n_txs": 1, "is_valid": True, "invalid_transactions": [],
        "expected_cardano_reject": spec["reject"],
        "note_for_codforge": "compute segwit body_hash+size; forge header slot>=1200 (97b0 leader) prev f4d474b8 height 234 forced-nonce 3a5e3601",
    }, indent=2) + "\n")
    (OUT / f"{label}-preflight.cbor").write_bytes(preflight_tx)
    print(f"[{label}] type {t} txid {txid[:16]} cred(unreg) {cred_hash.hex()[:16]} "
          f"seg_bytes {len(bytes.fromhex(seg_hex))} preflight_bytes {len(preflight_tx)}")
    print(f"    cert={cert if t!=18 else [18, credential, None]}")
    print(f"    preflight_cbor_hex={preflight_tx.hex()}")


if __name__ == "__main__":
    for lbl in (sys.argv[1:] or list(CASES)):
        build(lbl)
