"""Derive the proposal-anchor cases cardano-cli cannot build from the cardano-cli-built info-valid.tx.

Body edits of the single proposal's anchor (re-signed with the payment + return-account keys);
everything else stays VALID (return account registered in the same tx, deposit exact), so a
decoder that accepts the edge surfaces as an ACCEPT:
  anchor-url-129       anchor url of 129 bytes (Conway url = tstr .size (0..128)): decode edge.
  anchor-hash-31       anchor data hash of 31 bytes (hash32): decode edge.
  anchor-url-128-valid anchor url of exactly 128 bytes: boundary control (accept).
  return-wrong-network the return reward account's header set to MAINNET (0xe1) for the same key;
                       cardano-cli silently rewrites a mainnet --deposit-return-stake-address to
                       the --testnet network, so this case cannot be built with the CLI.

Self-checks (txedit.self_check), or abort.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cbor2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import txedit  # noqa: E402

PROPOSALS = 20  # Conway tx body key: proposal_procedures


def main() -> None:
    body, wits_b, tail = txedit.split_tx(txedit.load(HERE, "info-valid.tx"))
    wits = cbor2.loads(wits_b)
    signers = [txedit.key(HERE, "payment"), txedit.key(HERE, "ret")]
    txedit.self_check(body, wits, signers)
    base = cbor2.loads(body)

    def case(name, anchor, desc, account=None):
        b = dict(base)
        procs = txedit.items(base[PROPOSALS])
        deposit, acct, action, _ = procs[0]
        b[PROPOSALS] = txedit.rewrap(base[PROPOSALS], [[deposit, account or acct, action, anchor]])
        enc = cbor2.dumps(b)
        txedit.write(HERE, name, enc, txedit.signed_wits(enc, wits, signers, []), tail, desc)

    url, digest = txedit.items(base[PROPOSALS])[0][3]
    stem = "https://dwarf.example/"
    case("anchor-url-129", ["%s%s" % (stem, "p" * (129 - len(stem))), digest],
         "proposal anchor url of 129 bytes (max 128)")
    case("anchor-url-128-valid", ["%s%s" % (stem, "p" * (128 - len(stem))), digest],
         "proposal anchor url of exactly 128 bytes (boundary)")
    case("anchor-hash-31", [url, bytes(digest)[:31]], "proposal anchor data hash of 31 bytes")
    acct = bytes(txedit.items(base[PROPOSALS])[0][1])
    assert acct[0] == 0xE0, "expected a testnet key-hash reward account"
    case("return-wrong-network", [url, digest], "return reward account on MAINNET (header 0xe1)",
         account=b"\xe1" + acct[1:])
    print("edits: wrote 4 derived cases")


if __name__ == "__main__":
    main()
