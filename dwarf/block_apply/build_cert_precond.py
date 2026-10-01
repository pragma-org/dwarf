#!/usr/bin/env python3
"""R1a: cert-precondition expansion beyond the 4 done deleg/update types. Each cert references a
NEVER-REGISTERED / non-member entity that cardano-node rejects (*NotRegistered* / not-a-member);
tests whether amaru's bind_left also accepts these (extending the missing-registration class) or
guards them (clean-negative). Fresh keys so the tx is otherwise well-witnessed. 4-element Conway tx.

Conway cert encodings probed:
  1  stake_deregistration (deprecated)  [1, stake_cred]                 dereg of UNregistered
  8  unreg_cert (w/ deposit)            [8, stake_cred, coin]           unreg of UNregistered
  4  pool_retirement                    [4, pool_keyhash, epoch]        retire of UNregistered pool
  17 unreg_drep                         [17, drep_cred, coin]           unreg of UNregistered DRep
  14 auth_committee_hot                 [14, cold_cred, hot_cred]       auth-hot for NON-member cold
  15 resign_committee_cold              [15, cold_cred, anchor/null]    resign of NON-member cold
"""
import json, cbor2, hashlib, sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "antithesis/cardano_amaru_adversarial/fixture"
sys.path.insert(0, str(FIX / "collateral"))
from redeemers import split_tx  # noqa: E402
OUT = Path("/home/nigel/forge-work/pathb/outputs/forged-block/r1a")
STAKE_DEPOSIT = 2000000
DREP_DEPOSIT = 500000000


def raw_vkey(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def base_parts():
    seed = bytes.fromhex(json.loads((FIX / "funding" / "payment.skey").read_text())["cborHex"])[2:]
    pay = Ed25519PrivateKey.from_private_bytes(seed)
    raw = bytes.fromhex(json.loads((FIX / "mempool" / "mp-base.tx").read_text())["cborHex"])
    b, w, tail = split_tx(raw)
    base = cbor2.loads(b); tmpl = cbor2.loads(w)
    return pay, tmpl, base[0], bytes(base[1][0][0]), (base[1][0][1] if isinstance(base[1][0][1], int) else base[1][0][1][0]), base[2]


def rewrap(orig, lst):
    return cbor2.CBORTag(orig.tag, lst) if isinstance(orig, cbor2.CBORTag) else lst


def kh(k):
    return hashlib.blake2b(raw_vkey(k), digest_size=28).digest()


CASES = {
    # label: (builder(fresh_key)-> (cert, extra_signers), cardano_expected)
    "dereg-unregistered":        (lambda k: ([1, [0, kh(k)]], [k]),                         "StakeKeyNotRegisteredDEREG"),
    "unreg-unregistered":        (lambda k: ([8, [0, kh(k)], STAKE_DEPOSIT], [k]),          "StakeKeyNotRegisteredDEREG / incorrect deposit"),
    "pool-retire-unregistered":  (lambda k: ([4, kh(k), 5], [k]),                           "StakePoolNotRegisteredOnKeyPOOL"),
    "unreg-drep-unregistered":   (lambda k: ([17, [0, kh(k)], DREP_DEPOSIT], [k]),          "ConwayDRepNotRegistered"),
    "committee-auth-nonmember":  (lambda k: ([14, [0, kh(k)], [0, kh(Ed25519PrivateKey.generate())]], [k]), "committee not authorized / not a member"),
    "committee-resign-nonmember":(lambda k: ([15, [0, kh(k)], None], [k]),                  "committee not a member"),
}


def build(label):
    make, c_exp = CASES[label]
    pay, tmpl, inputs, fund_addr, val, fee = base_parts()
    cred_key = Ed25519PrivateKey.generate()
    cert, signers = make(cred_key)
    body = {0: inputs, 1: [[fund_addr, val]], 2: fee, 4: [cert]}
    body_b = cbor2.dumps(body)
    h = hashlib.blake2b(body_b, digest_size=32).digest()
    vk_wits = [[raw_vkey(pay), pay.sign(h)]] + [[raw_vkey(s), s.sign(h)] for s in signers]
    wits = {0: rewrap(tmpl[0], vk_wits)}
    preflight = cbor2.dumps([body, wits, True, None])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}-preflight.cbor").write_bytes(preflight)
    txid = hashlib.blake2b(body_b, digest_size=32).hexdigest()
    print(f"[{label}] cert[0]={cert[0]} txid {txid[:16]} cardano_expect={c_exp} bytes {len(preflight)}")


if __name__ == "__main__":
    for lbl in (sys.argv[1:] or list(CASES)):
        build(lbl)
