"""Edit the redeemers of a valid unsigned Conway tx for the two cases cardano-cli cannot build.

  missing-redeemer: drop the mint redeemer. The ledger still expects a script-integrity hash
                    while a Plutus script is needed (languageViews is non-empty), computed over
                    an EMPTY redeemer map (a0); that is what the body carries, so only the
                    missing redeemer is wrong (verified live: removing the hash instead adds
                    PPViewHashesDontMatch on cardano-node).
  extra-redeemer:   add a spend redeemer for input 0 (key-locked, no script), keep the mint one,
                    and recompute the script-integrity hash so only ExtraRedeemers applies.

The body bytes are edited in place (only the 32-byte integrity hash changes), so
tag-258 sets and field order survive. The integrity hash is recomputed from pparams.json and the
function is SELF-CHECKED first: it must reproduce the hash cardano-cli wrote into the unmodified
tx, or the script aborts.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys

import cbor2

PLUTUS_V3 = 2  # language id in the language-views map


def split_tx(raw: bytes) -> tuple[bytes, bytes, bytes]:
    """Return (body_bytes, witness_bytes, tail_bytes) of a 4-element Conway tx."""
    assert raw[0] == 0x84, "expected a definite 4-element tx array"
    stream = io.BytesIO(raw[1:])
    decoder = cbor2.CBORDecoder(stream)
    decoder.decode()
    body_end = 1 + stream.tell()
    decoder.decode()
    wits_end = 1 + stream.tell()
    return raw[1:body_end], raw[body_end:wits_end], raw[wits_end:]


def integrity_hash(redeemers: dict, pparams: dict) -> bytes:
    views = cbor2.dumps({PLUTUS_V3: pparams["costModels"]["PlutusV3"]})
    return hashlib.blake2b(cbor2.dumps(redeemers) + views, digest_size=32).digest()


def rewrite(path: str, mode: str, pparams: dict) -> None:
    envelope = json.load(open(path))
    raw = bytes.fromhex(envelope["cborHex"])
    body_bytes, wits_bytes, tail = split_tx(raw)
    body, wits = cbor2.loads(body_bytes), cbor2.loads(wits_bytes)
    old_hash = body[11]
    if integrity_hash(wits[5], pparams) != old_hash:
        raise SystemExit(f"{path}: cannot reproduce cardano-cli's script-integrity hash; refusing to edit")
    if cbor2.dumps(wits) != wits_bytes:
        raise SystemExit(f"{path}: witness set does not round-trip through cbor2; refusing to edit")
    hash_field = b"\x0b\x58\x20" + old_hash          # key 11, bytes(32)
    assert body_bytes.count(hash_field) == 1
    if mode == "missing":
        del wits[5]
        new_body = body_bytes.replace(hash_field, b"\x0b\x58\x20" + integrity_hash({}, pparams))
    else:
        wits[5][(0, 0)] = [cbor2.CBORTag(121, []), [1000000, 500000000]]
        new_body = body_bytes.replace(hash_field, b"\x0b\x58\x20" + integrity_hash(wits[5], pparams))
    new = b"\x84" + new_body + cbor2.dumps(wits) + tail
    check_body, check_wits = cbor2.loads(split_tx(new)[0]), cbor2.loads(split_tx(new)[1])
    assert 11 in check_body and (5 in check_wits) == (mode != "missing")
    envelope["cborHex"] = new.hex()
    json.dump(envelope, open(path, "w"), indent=4)


if __name__ == "__main__":
    missing, extra, pparams_path = sys.argv[1:4]
    params = json.load(open(pparams_path))
    rewrite(missing, "missing", params)
    rewrite(extra, "extra", params)
    print("redeemer edits: ok (integrity hash self-check passed)")
