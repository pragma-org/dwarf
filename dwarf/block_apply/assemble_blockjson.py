#!/usr/bin/env python3
"""Assemble block.json (wire's locked schema) from cod-forge's forged header + body, and VALIDATE.

MILESTONE 1 (empty body): forge-result.json needs only forged_header_cbor_hex + slot; body is the
empty template body and the forged header must commit body_hash=29571d16.../body_size=4.
MILESTONE 2 (crafted body): forge-result.json ALSO provides body_segments_cbor_hex + body_hash +
body_size (cod-forge computes the Conway segwit body hash with cardano libs); the forged header must
commit exactly those; this script builds the block with the real body.

Usage: assemble_blockjson.py    (reads outputs/forged-block/forge-result.json)
Writes outputs/forged-block/block.json (aborts on any validation FAIL).
"""
import cbor2, hashlib, json, sys
from pathlib import Path

PB = Path("/home/nigel/forge-work/pathb")
OUT = PB / "outputs" / "forged-block"
tpl = json.load(open(PB / "template-97b0-empty-block.json"))
ERA = tpl["era"]  # 7 = Conway
PREV = "f4d474b8498a97a884f84b32ceede03bf392abae870a2b30b566f722192ff7bd"
HEIGHT = 234


def hx(x):
    return bytes(x).hex() if not isinstance(x, str) else x


def main():
    fr = json.load(open(OUT / "forge-result.json"))
    hdr_hex = fr["forged_header_cbor_hex"].strip()
    slot = int(fr["slot"])
    height = int(fr.get("block_height", HEIGHT))

    # body: M2 provides body_segments_cbor_hex + body_hash/size; M1 falls back to the empty template
    if fr.get("body_segments_cbor_hex"):
        body_segs = cbor2.loads(bytes.fromhex(fr["body_segments_cbor_hex"]))
        exp_body_hash = fr["body_hash"].lower()
        exp_body_size = int(fr["body_size"])
        milestone = "M2 (crafted body)"
    else:
        body_segs = cbor2.loads(bytes.fromhex(tpl["body_segments_cbor_hex"]))
        exp_body_hash = tpl["body_hash"].lower()
        exp_body_size = int(tpl["body_size"])
        milestone = "M1 (empty body)"

    forged = cbor2.loads(bytes.fromhex(hdr_hex))
    hb = forged[0]
    checks = {
        "block_no==%d" % height: hb[0] == height,
        "slot==%d" % slot: hb[1] == slot,
        "prev_hash==tip": (hb[2] is not None and hx(hb[2]) == PREV),
        "header.body_size==body": hb[6] == exp_body_size,
        "header.body_hash==body": hx(hb[7]).lower() == exp_body_hash,
    }
    print("=== %s : forged header validation ===" % milestone)
    for k, v in checks.items():
        print(("  OK  " if v else "  FAIL") + " " + k)
    if not all(checks.values()):
        print("ABORT: forged header fails invariants; not assembling")
        sys.exit(3)

    era_block = [ERA, [forged] + list(body_segs)]
    era_block_bytes = cbor2.dumps(era_block)
    tag24_bytes = cbor2.dumps(cbor2.CBORTag(24, era_block_bytes))
    point_hash = hashlib.blake2b(bytes.fromhex(hdr_hex), digest_size=32).hexdigest()

    out = {
        "header_cbor_hex": hdr_hex,
        "block_cbor_hex": era_block_bytes.hex(),
        "block_cbor_tag24_hex": tag24_bytes.hex(),
        "point_slot": slot,
        "point_hash_hex": point_hash,
        "block_height": height,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "block.json").write_text(json.dumps(out, indent=2) + "\n")
    print("=== wrote", OUT / "block.json", "(%s) ===" % milestone)
    print("point_slot", slot, "point_hash", point_hash, "height", height, "era", ERA)


if __name__ == "__main__":
    main()
