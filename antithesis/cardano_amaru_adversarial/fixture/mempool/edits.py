"""Derive the max-tx-size boundary cases from the cardano-cli-built mp-base.tx.

The ledger size both nodes check against maxTxSize (16384) is 1 + body + witnesses + aux, IsValid
excluded, all counted on the ORIGINAL bytes (cardano-ledger sizeTxF; amaru TransactionRef::len()).
Each variant is padded with metadata so that its ORIGINAL ledger size is exactly 16385 (reject)
or 16384 (accept), while the component under test is encoded NON-canonically, so its canonical
re-encoding is SMALLER than the original bytes:

  canonical   no bloat (reference pair)
  body        fee as a 9-byte uint (1b 00000000000f4240) instead of 5 bytes (+4)
  wits        vkey-witness array as indefinite length (9f .. ff) instead of 81 (+1)
  aux         metadata map indefinite (bf .. ff, +1) and 8 text heads non-minimal (79 0040, +8)

A node that measured a re-encoding instead of the original bytes would accept the -16385 variant
(its canonical size is under the cap). Every tx is otherwise valid: ADA balanced, fee 1 ADA
(>> min 876 277 at 16384 bytes), metadata hash over the aux as sent, payment-signed.

Self-checks (txedit.self_check) and per-case size assertions, or abort.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import cbor2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import txedit  # noqa: E402

MAX_TX_SIZE = 16384
FEE = 1_000_000
CANON_FEE = b"\x02\x1a\x00\x0f\x42\x40"                     # key 2 (fee) => 1000000, minimal
BLOAT_FEE = b"\x02\x1b\x00\x00\x00\x00\x00\x0f\x42\x40"     # same value, 8-byte argument
LABEL = cbor2.dumps(674)


def text(s: str, bloat: bool = False) -> bytes:
    b = s.encode()
    if bloat:  # 2-byte-argument head for a < 256-byte string: 1 byte longer than minimal
        return b"\x79" + len(b).to_bytes(2, "big") + b
    return cbor2.dumps(s)


def aux_bytes(n: int, last: int, bloat: bool, ints: int = 0) -> bytes:
    # ints: up to 3 one-byte metadatum 0s, closing the 1-byte gaps between text-head sizes
    chunks = [text("a" * 64, bloat and i < 8) for i in range(n)] + ([text("b" * last)] if last else []) \
        + [b"\x00"] * ints
    assert 24 <= len(chunks) < 256
    lst = bytes([0x98, len(chunks)])              # array head, 1-byte length argument
    inner = b"\xa1\x63msg" + lst + b"".join(chunks)  # {"msg": [chunks...]}
    md = (b"\xbf" + LABEL + inner + b"\xff") if bloat else (b"\xa1" + LABEL + inner)
    return b"\xd9\x01\x03\xa1\x00" + md          # tag 259 {0: metadata}


def main() -> None:
    body0, wits_b, tail = txedit.split_tx(txedit.load(HERE, "mp-base.tx"))
    wits = cbor2.loads(wits_b)
    pay = txedit.key(HERE, "payment")
    txedit.self_check(body0, wits, [pay])
    base = cbor2.loads(body0)
    assert base[2] == FEE and CANON_FEE in body0

    def build(variant: str, n: int, last: int, ints: int = 0):
        aux = aux_bytes(n, last, variant == "aux", ints)
        b = dict(base)
        b[7] = hashlib.blake2b(aux, digest_size=32).digest()
        body = cbor2.dumps(b)
        if variant == "body":
            assert body.count(CANON_FEE) == 1
            body = body.replace(CANON_FEE, BLOAT_FEE)
        h = hashlib.blake2b(body, digest_size=32).digest()
        vk = txedit.vkey(pay)
        wit = cbor2.dumps([vk, pay.sign(h)])
        wset = (b"\xa1\x00\x9f" + wit + b"\xff") if variant == "wits" else (b"\xa1\x00\x81" + wit)
        original = 1 + len(body) + len(wset) + len(aux)
        canonical = 1 + len(cbor2.dumps(cbor2.loads(body))) + len(b"\xa1\x00\x81" + wit) \
            + len(cbor2.dumps(cbor2.loads(aux)))
        return b"\x84" + body + wset + b"\xf5" + aux, original, canonical

    sizes = {}
    for variant in ("canonical", "body", "wits", "aux"):
        for target, suffix in ((MAX_TX_SIZE + 1, "16385"), (MAX_TX_SIZE, "16384-valid")):
            for n in range(200, 255):
                hit = next(((n, last, k) for k in range(4) for last in range(0, 65)
                            if build(variant, n, last, k)[1] == target), None)
                if hit:
                    break
            assert hit, (variant, target)
            tx, original, canonical = build(variant, *hit)
            if variant == "canonical":
                assert canonical == original
            else:
                assert canonical < original and canonical <= MAX_TX_SIZE, (variant, canonical)
            name = f"size-{variant}-{suffix}"
            (HERE / f"{name}.tx").write_text(json.dumps(
                {"type": "Tx ConwayEra", "description": f"{variant}: ledger size {original} "
                 f"(canonical re-encoding {canonical})", "cborHex": tx.hex()}, indent=4) + "\n")
            sizes[name] = {"original_ledger_size": original, "canonical_ledger_size": canonical}
    (HERE / "sizes.json").write_text(json.dumps(sizes, indent=1) + "\n")
    print("edits: wrote", len(sizes), "size cases")


if __name__ == "__main__":
    main()
