"""Craft a spread of non-canonical / malformed PlutusData redeemer variants (splice into the
witness set, body untouched -> no re-sign). always-succeeds ignores the redeemer, so every
DECODABLE variant trips the script-integrity hash (both nodes); a variant a STRICT decoder
rejects but a lenient one accepts would show a decode-strictness split (decode_reject vs
integrity_hash, or accept)."""
import json, io
import cbor2

P = "/tmp/nsprobe/pd"
tx = bytes.fromhex(json.load(open(f"{P}/base-plutusmint.tx"))["cborHex"])
s = io.BytesIO(tx[1:]); d = cbor2.CBORDecoder(s)
sp = []
for _ in range(4):
    a = s.tell(); d.decode(); b = s.tell(); sp.append((1 + a, 1 + b))
body, wits, tail = tx[sp[0][0]:sp[0][1]], tx[sp[1][0]:sp[1][1]], tx[sp[2][0]:sp[3][1]]
CANON = bytes.fromhex("d87980")
i = wits.find(CANON); assert i >= 0 and wits.count(CANON) == 1

VARIANTS = {
  "pd-indefinite-array": "d8799fff",                       # tag121 indefinite empty array (same unit value)
  "pd-nonminimal-tag":   "d9007980",                       # tag121 via non-minimal 2-byte arg
  "pd-nonminimal-int":   "1b0000000000000000",             # int 0 as non-minimal uint64
  "pd-indefinite-bytes": "5f4161ff",                       # bytes 0x61 via indefinite chunks
  "pd-dup-map-key":      "a2000000 00".replace(" ", ""),   # map{0:0, 0:0} duplicate key 0 (malformed)
  "pd-unchunked-65b":    "5841" + "61" * 65,               # 65-byte bytestring single chunk (>64: non-canonical for Plutus)
}

for name, hx in VARIANTS.items():
    pd = bytes.fromhex(hx)
    nw = wits[:i] + pd + wits[i + len(CANON):]
    ntx = b"\x84" + body + nw + tail
    json.dump({"type": "Tx ConwayEra", "description": name, "cborHex": ntx.hex()}, open(f"{P}/{name}.tx", "w"))
    print("wrote", name, "->", hx)
