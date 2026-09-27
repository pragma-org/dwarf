#!/usr/bin/env python3
# CBOR decode-strictness batch 2: bytestring encodings, set-tag handling, key ordering, non-minimal
# length prefixes. Focus on witness-set (non-body -> sig-preserving) for clean amaru/cardano divergence.
import urllib.request, sys, os, time
AM="http://localhost:3210/api/submit/tx"; CN="http://localhost:8110/api/submit/tx"
BASE=bytes.fromhex(
"84a300d90102818258209708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1"
"00018182581d60e5a5ddb03fe37059627fede71458401e0dd97bb371b3bd7cce0d23271b0000b5e620f1fe7f"
"021a00028181a100d901028182582016ca8eeb26ae9774130d27a8c6f166d7f8e3ca0f9cc79b349e5077774217daf8"
"584079e5731a989adcf7e83b2cf1657680c0fbda83b0b11d73c34021664f956eda9a98303751cfd733ede22aeb5a099c185eaf66368392276787609b086bd4c01806"
"f5f6")
# offsets: witness set tag d90102 @94-96, arr 81 @97, elem 82 @98, vkey 5820 @99-100 +32(@101-132),
# sig 5840 @133-134 +64(@135-198), is_valid f5 @199
def submit(url, raw):
    req=urllib.request.Request(url, data=raw, headers={"Content-Type":"application/cbor"}, method="POST")
    try:
        r=urllib.request.urlopen(req, timeout=20); return r.status, r.read().decode("utf8","replace")
    except urllib.error.HTTPError as e: return e.code, e.read().decode("utf8","replace")
    except Exception as e: return 0, "ERR:"+str(e)
def classify(code, body):
    if code==202: return "ACCEPT"
    if code==0: return "UNAVAILABLE"
    b=body.lower()
    ledger=any(s in b for s in ["utxo","valuenotconserved","feetoosmall","below minimum","missing","script",
        "outsidevalidity","badinputs","conwayutxow","conwaymempool","all inputs are spent","phase one","phase two","witness"])
    decode=any(s in b for s in ["deserialise","decoder","decode error","invalid cbor","failed to decode",
        "end of input","unexpected","expected","cbor","malformed","not enough bytes","trailing","canonical",
        "duplicate","indefinite","size mismatch","bytes remaining","leftover"])
    if decode and not ledger: return "DECODE_REJECT"
    if code==400: return "VALIDATION_REJECT"
    return "MASKED"

def vkey_chunked(b):   # vkey 5820<32> @99 -> 5f 5810<16> 5810<16> ff  (indefinite/chunked bytestring)
    x=bytearray(b); assert x[99]==0x58 and x[100]==0x20
    vk=bytes(x[101:133]); rep=b"\x5f\x58\x10"+vk[:16]+b"\x58\x10"+vk[16:]+b"\xff"
    return bytes(x[:99])+rep+bytes(x[133:])
def sig_nonmin_len(b): # sig 5840<64> @133 -> 59 0040 <64> (2-byte length, non-minimal)
    x=bytearray(b); assert x[133]==0x58 and x[134]==0x40
    return bytes(x[:133])+b"\x59\x00\x40"+bytes(x[135:])
def vkey_nonmin_len(b):# vkey 5820<32> @99 -> 59 0020 <32>
    x=bytearray(b); assert x[99]==0x58 and x[100]==0x20
    return bytes(x[:99])+b"\x59\x00\x20"+bytes(x[101:])
def witset_no_tag(b):  # witness set: drop tag258 (d90102 @94-96), keep array 81 -> plain array set
    x=bytearray(b); assert x[94]==0xd9 and x[95]==0x01 and x[96]==0x02
    return bytes(x[:94])+bytes(x[97:])
def witset_wrong_tag(b):# tag 258 (d90102) -> tag 259 (d90103)
    x=bytearray(b); assert x[94]==0xd9 and x[95]==0x01 and x[96]==0x02
    y=bytearray(x); y[96]=0x03; return bytes(y)
def inputs_no_tag(b):  # inputs set tag258 @3-5 -> drop, keep array 81
    x=bytearray(b); assert x[3]==0xd9 and x[4]==0x01 and x[5]==0x02
    return bytes(x[:3])+bytes(x[6:])
def body_key_reorder(b): # body map a3: reorder to fee(02) first -> 00,01 after. changes body enc (sig may fail)
    x=bytearray(b); assert x[1]==0xa3
    inp=bytes(x[2:43]); out=bytes(x[43:86]); fee=bytes(x[86:92])
    return bytes(x[:1])+b"\xa3"+fee+inp+out+bytes(x[92:])
def amount_nonmin(b):  # amount 1b<8bytes> @77 -> keep value but... it is already 1b(uint64). test non-minimal: value fits, encoded as 1b (8-byte). minimal for 0x0000b5e620f1fe7f needs 1b. skip->use fee value nonmin
    return b  # placeholder (amount already needs 8 bytes)

MUTS=[
 ("00-base-control", lambda b:b, "sanity both accept"),
 ("13-vkey-chunked-bytestring", vkey_chunked, "indefinite/chunked bytestring for vkey (non-body)"),
 ("14-sig-nonminimal-length", sig_nonmin_len, "non-minimal 2-byte length prefix on sig (non-body)"),
 ("15-vkey-nonminimal-length", vkey_nonmin_len, "non-minimal 2-byte length prefix on vkey (non-body)"),
 ("16-witset-no-258-tag", witset_no_tag, "witness set as plain array (tag258 omitted)"),
 ("17-witset-wrong-259-tag", witset_wrong_tag, "witness set wrapped in tag259 instead of 258"),
 ("18-inputs-no-258-tag", inputs_no_tag, "inputs set as plain array (tag258 omitted) - body"),
 ("19-body-key-reorder", body_key_reorder, "non-canonical body map key order (fee first)"),
]
print(f"{'case':<28} {'amaru':<20} {'cardano':<20} verdict")
print("-"*94)
div=[]
for name,fn,note in MUTS:
    try: raw=fn(BASE)
    except AssertionError: print(f"{name:<28} OFFSET-ASSERT-FAIL"); continue
    os.system("/home/nigel/reset-pair.sh pair1 >/dev/null 2>&1"); time.sleep(1)
    ac,ab=submit(AM,raw); cc,cb=submit(CN,raw)
    acl=classify(ac,ab); ccl=classify(cc,cb); agree=(acl==ccl)
    print(f"{name:<28} {acl+'('+str(ac)+')':<20} {ccl+'('+str(cc)+')':<20} {'AGREE' if agree else '*** DIVERGENCE ***'}")
    if not agree: div.append((name,note,acl,ac,ab[:260],ccl,cc,cb[:260]))
print()
for name,note,acl,ac,ab,ccl,cc,cb in div:
    print(f"### {name} — {note}\n  amaru   [{acl} {ac}]: {ab}\n  cardano [{ccl} {cc}]: {cb}\n")
if not div: print("No verdict-class divergence (all AGREE).")
