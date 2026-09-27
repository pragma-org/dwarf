"""Derive the mint/burn cases cardano-cli cannot build from the cardano-cli-built mint-valid.tx.

Witness-set edits (the body, hence every signature, is untouched):
  mint-script-missing    drop the policy native script: MissingScriptWitnesses.
  mint-script-wrong      replace it with a different native script: Missing + Extraneous.
  extraneous-script-no-mint  unminted-policy-free ADA-only tx carrying the policy script.

Body edits (re-signed with the payment + policy keys). Each keeps the rest of the tx VALID
(ADA balanced, policy witnessed), so a decoder that accepts the edge surfaces as an ACCEPT:
  burn-int64-min         mint -2^63 of MINT, ADA-only output.
  mint-zero-qty          mint 0 of MINT (Conway: nonZeroInt64), ADA-only output.
  mint-empty-asset-map   mint {policy: {}} (Conway: non-empty inner map), ADA-only output.
  mint-empty-map         mint {} (Conway: non-empty mint), ADA-only output, no script.
  asset-name-33b         mint 10 of a 33-byte asset name, carried by the output.
  output-zero-qty        output carries 0 of a policy token (Conway: positive_coin), no mint.

Self-checks (txedit.self_check), or abort: cbor2 re-encoding of the unedited body is
byte-identical, and the Python Ed25519 signatures equal the cardano-cli witnesses.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cbor2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import txedit  # noqa: E402
from txedit import items, signed_wits, split_tx  # noqa: E402

MINT = b"MINT"


def load(name: str) -> bytes:
    return txedit.load(HERE, name)


def key(name: str):
    return txedit.key(HERE, name)


def write(name: str, body: bytes, wits: dict, tail: bytes, desc: str) -> None:
    txedit.write(HERE, name, body, wits, tail, desc)


def main() -> None:
    body, wits_b, tail = split_tx(load("mint-valid.tx"))
    wits = cbor2.loads(wits_b)
    pay, pol = key("payment"), key("policy")
    txedit.self_check(body, wits, [pay, pol])
    policy_script = items(wits[1])[0]
    other = json.loads((HERE / "other.script").read_text())["scripts"][0]["keyHash"]
    other_script = [1, [[0, bytes.fromhex(other)]]]  # all [sig other]

    # --- witness-set edits (signatures stay valid: the body is unchanged) ---
    # the vkey set is spliced as raw bytes (cbor2 set order is not reproducible, see txedit.raw_map)
    raw = txedit.raw_map(wits_b)
    write("mint-script-missing", body, txedit.encode_raw_map({0: raw[0]}), tail,
          "policy native script omitted")
    write("mint-script-wrong", body,
          txedit.encode_raw_map({0: raw[0], 1: cbor2.dumps([other_script])}), tail,
          "a different native script supplied in place of the policy script")
    # an ADA-only, otherwise-valid body derived from mint-valid (drop mint, ADA-only output);
    # outputs keep the shape cardano-cli wrote (legacy [addr, value] or post-Babbage {0:, 1:})
    base = cbor2.loads(body)
    out0 = base[1][0]
    legacy = isinstance(out0, list)
    out_addr, out_val = out0[0], out0[1]  # same indices for both shapes
    ada = out_val[0] if isinstance(out_val, list) else out_val

    def output(value):
        return [out_addr, value] if legacy else {0: out_addr, 1: value}

    def ada_only(over=None):
        b = dict(base)
        b[1] = [output(ada)]
        b.pop(9, None)
        b.update(over or {})
        return b

    plain = cbor2.dumps(ada_only())
    write("extraneous-script-no-mint", plain,
          signed_wits(plain, wits, [pay], [policy_script]), tail,
          "no mint; policy native script supplied anyway")

    # --- body edits (re-signed; everything else valid) ---
    pid = next(iter(base[9]))

    def case(name, b, signers, scripts, desc):
        enc = cbor2.dumps(b)
        write(name, enc, signed_wits(enc, wits, signers, scripts), tail, desc)

    case("burn-int64-min", ada_only({9: {pid: {MINT: -(2 ** 63)}}}), [pay, pol], [policy_script],
         "burn -2^63 of MINT, no token input")
    case("mint-zero-qty", ada_only({9: {pid: {MINT: 0}}}), [pay, pol], [policy_script],
         "mint quantity 0 (Conway mint is nonZeroInt64)")
    case("mint-empty-asset-map", ada_only({9: {pid: {}}}), [pay, pol], [policy_script],
         "mint {policy: {}} (Conway inner asset map is non-empty)")
    case("mint-empty-map", ada_only({9: {}}), [pay], [], "mint {} (Conway mint is non-empty)")
    name33 = b"MINT" + b"X" * 29
    case("asset-name-33b", ada_only({1: [output([ada, {pid: {name33: 10}}])],
                                       9: {pid: {name33: 10}}}),
         [pay, pol], [policy_script], "mint + output 10 of a 33-byte asset name (max 32)")
    case("output-zero-qty", ada_only({1: [output([ada, {pid: {MINT: 0}}])]}), [pay], [],
         "output carries 0 of a policy token (Conway outputs are positive_coin); no mint")
    print("edits: wrote 9 derived cases")


if __name__ == "__main__":
    main()
