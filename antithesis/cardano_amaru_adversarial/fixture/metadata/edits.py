"""Derive the metadata-validation cases from the cardano-cli-built md-base.tx.

The auxiliary data is written as RAW bytes (a hand encoder, not cbor2), so non-canonical forms are
exact. The Alonzo aux form cardano-cli emits is kept: tag 259 {0: metadata}. Every case keeps the
rest of the tx valid and re-signs the body (the body carries auxiliary_data_hash, key 7) with the
payment key, except aux-missing, whose body is unchanged.

  hash over the bytes AS SENT vs over their canonical re-encoding (the ledger hashes the original
  bytes, so the first must be accepted and the second rejected with ConflictingMetadataHash):
    noncanon-uint-hash-sent / -hash-canonical     label 674 as a 5-byte uint (1a000002a2)
    noncanon-indef-hash-sent / -hash-canonical    indefinite-length metadata map and list
  hash rules: hash-mismatch, hash-missing (aux present, key 7 absent), aux-missing (key 7 present,
    aux null)
  size rules (InvalidMetadata, 64-byte limit): text-65, bytes-65, nested-text-65; controls
    text-64-valid, bytes-64-valid, canonical-valid
  edges (expected verdict set from the ledger CDDL, see manifest.py): bignum-small, int-overflow,
    dup-labels, indef-text

Self-checks (txedit.self_check), or abort; and the canonical aux bytes built here must equal the
aux bytes cardano-cli wrote into md-base.tx, so the hand encoder is anchored to the CLI.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import cbor2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import txedit  # noqa: E402

AUX_HASH = 7


def head(major: int, n: int) -> bytes:
    """Minimal (canonical) CBOR head."""
    if n < 24:
        return bytes([major << 5 | n])
    for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | ai]) + n.to_bytes(size, "big")
    raise ValueError(n)


def uint(n): return head(0, n)
def text(s): b = s.encode(); return head(3, len(b)) + b
def byts(b): return head(2, len(b)) + b
def arr(*xs): return head(4, len(xs)) + b"".join(xs)
def mapp(*kvs): return head(5, len(kvs)) + b"".join(k + v for k, v in kvs)
def alonzo(md: bytes) -> bytes: return b"\xd9\x01\x03" + mapp((uint(0), md))  # tag 259 {0: md}


LABEL = uint(674)
MSG = mapp((text("msg"), arr(text("hello"))))  # {"msg": ["hello"]}
CANON = alonzo(mapp((LABEL, MSG)))


def blake(b: bytes) -> bytes:
    return hashlib.blake2b(b, digest_size=32).digest()


def main() -> None:
    body, wits_b, tail = txedit.split_tx(txedit.load(HERE, "md-base.tx"))
    wits = cbor2.loads(wits_b)
    pay = txedit.key(HERE, "payment")
    txedit.self_check(body, wits, [pay])
    assert tail == b"\xf5" + CANON, "hand-encoded canonical aux differs from cardano-cli's"
    base = cbor2.loads(body)
    assert base[AUX_HASH] == blake(CANON)

    def case(name, aux: bytes | None, desc, aux_hash=None, drop_hash=False):
        b = dict(base)
        if drop_hash:
            b.pop(AUX_HASH)
        else:
            b[AUX_HASH] = aux_hash if aux_hash is not None else blake(aux)
        enc = cbor2.dumps(b)
        new_tail = b"\xf5" + (aux if aux is not None else b"\xf6")
        txedit.write(HERE, name, enc, txedit.signed_wits(enc, wits, [pay], []), new_tail, desc)

    # --- hash over the sent bytes vs over the canonical re-encoding ---
    nc_uint = alonzo(mapp((b"\x1a\x00\x00\x02\xa2", MSG)))  # 674 as a 4-byte-argument uint
    nc_indef = alonzo(b"\xbf" + LABEL + mapp((text("msg"), b"\x9f" + text("hello") + b"\xff")) + b"\xff")
    for tag, aux in (("uint", nc_uint), ("indef", nc_indef)):
        assert aux != CANON and cbor2.loads(aux).value == cbor2.loads(CANON).value  # same value
        case(f"noncanon-{tag}-hash-sent", aux, f"non-canonical ({tag}) aux; hash over the SENT bytes")
        case(f"noncanon-{tag}-hash-canonical", aux,
             f"non-canonical ({tag}) aux; hash over the CANONICAL re-encoding", aux_hash=blake(CANON))
    # --- hash rules ---
    case("canonical-valid", CANON, "canonical aux, hash over it (encoder control)")
    case("hash-mismatch", CANON, "aux hash of unrelated bytes", aux_hash=blake(b"dwarf"))
    case("hash-missing", CANON, "aux present, body auxiliary_data_hash absent", drop_hash=True)
    case("aux-missing", None, "body auxiliary_data_hash present, aux data null", aux_hash=blake(CANON))
    # --- 64-byte size rule ---
    msg = lambda v: alonzo(mapp((LABEL, mapp((text("msg"), arr(v))))))  # noqa: E731
    case("text-65", msg(text("a" * 65)), "metadata text of 65 bytes")
    case("text-64-valid", msg(text("a" * 64)), "metadata text of 64 bytes (boundary)")
    case("bytes-65", msg(byts(b"\xab" * 65)), "metadata bytes of 65 bytes")
    case("bytes-64-valid", msg(byts(b"\xab" * 64)), "metadata bytes of 64 bytes (boundary)")
    case("nested-text-65", msg(arr(text("x"), mapp((text("k"), text("b" * 65))))),
         "65-byte text nested in a list inside a map")
    # --- encoding edges ---
    case("bignum-small", msg(b"\xc2\x41\x05"), "metadatum int 5 encoded as a tag-2 bignum")
    case("int-overflow", msg(b"\xc2\x49\x01" + b"\x00" * 8), "metadatum int 2^64 (tag-2 bignum)")
    case("dup-labels", alonzo(mapp((LABEL, MSG), (LABEL, MSG))), "metadata map with label 674 twice")
    case("indef-text", msg(b"\x7f" + text("he") + text("llo") + b"\xff"),
         "metadata text as an indefinite-length (chunked) string")
    print("edits: wrote 19 derived cases")


if __name__ == "__main__":
    main()
