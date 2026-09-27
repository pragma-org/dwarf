#!/usr/bin/env python3
# CBOR decode-strictness differential: amaru :3210 vs cardano 11.1.2 :8110.
# Manual byte-craft of non-canonical encodings over TX-STRUCTURE fields; classify
# decode_reject vs validation_reject vs accept; fail-closed.
import urllib.request, json, sys, os, time

AM="http://localhost:3210/api/submit/tx"; CN="http://localhost:8110/api/submit/tx"
BASE=bytes.fromhex(
"84a300d90102818258209708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1"
"00018182581d60e5a5ddb03fe37059627fede71458401e0dd97bb371b3bd7cce0d23271b0000b5e620f1fe7f"
"021a00028181a100d901028182582016ca8eeb26ae9774130d27a8c6f166d7f8e3ca0f9cc79b349e5077774217daf8"
"584079e5731a989adcf7e83b2cf1657680c0fbda83b0b11d73c34021664f956eda9a98303751cfd733ede22aeb5a099c185eaf66368392276787609b086bd4c01806"
"f5f6")

def submit(url, raw):
    req=urllib.request.Request(url, data=raw, headers={"Content-Type":"application/cbor"}, method="POST")
    try:
        r=urllib.request.urlopen(req, timeout=20); return r.status, r.read().decode("utf8","replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf8","replace")
    except Exception as e:
        return 0, "ERR:"+str(e)

def classify(code, body):
    if code==202: return "ACCEPT"
    b=body.lower()
    if code==0: return "UNAVAILABLE"
    # decode-layer signals
    if any(s in b for s in ["deserialisefailure","deserialise","decoder","decode error","invalid cbor",
        "failed to decode","end of input","unexpected","expected","cbor","malformed","not enough bytes",
        "trailing","canonical","duplicate","indefinite"]):
        # distinguish decode from ledger: ledger errors have specific tokens
        if any(s in b for s in ["utxo","valuenotconserved","feetoosmall","below minimum","missing","script",
            "outsidevalidity","badinputs","conwayutxow","conwaymempool","all inputs are spent","phase one","phase two"]):
            return "VALIDATION_REJECT"
        return "DECODE_REJECT"
    if code==400: return "VALIDATION_REJECT"
    return "MASKED"

# ---- mutation builders (base is 201 bytes; offsets from manual parse) ----
def m_trailing(b):        return b + b"\x00"
def m_body_indef(b):      # a3(off1) -> bf ; insert ff before witness a1(off92)
    x=bytearray(b); assert x[1]==0xa3; x[1]=0xbf; x[92:92]=b"\xff"; return bytes(x)
def m_outer_indef(b):     # 84(off0) -> 9f ; append ff at end
    x=bytearray(b); assert x[0]==0x84; x[0]=0x9f; return bytes(x)+b"\xff"
def m_wit_map_indef(b):   # a1(off92) -> bf ; insert ff before is_valid f5(off199)
    x=bytearray(b); assert x[92]==0xa1; x[92]=0xbf; x[199:199]=b"\xff"; return bytes(x)
def m_inputs_arr_indef(b):# inputs 81(off6) -> 9f ; insert ff after the single input (before key 01 at off43)
    x=bytearray(b); assert x[6]==0x81; x[6]=0x9f; x[43:43]=b"\xff"; return bytes(x)
def m_fee_key_nonmin(b):  # fee key 02(off86) -> 18 02 (non-minimal uint)
    x=bytearray(b); assert x[86]==0x02; x[86:87]=b"\x18\x02"; return bytes(x)
def m_witkey_nonmin(b):   # witness map key 00(off93) -> 18 00 (non-minimal)
    x=bytearray(b); assert x[93]==0x00; x[93:94]=b"\x18\x00"; return bytes(x)
def m_dupkey_body(b):     # a3 -> a4, append duplicate fee entry (02 1a00028181) before witness a1(off92)
    x=bytearray(b); assert x[1]==0xa3; x[1]=0xa4; x[92:92]=bytes.fromhex("021a00028181"); return bytes(x)
def m_fee_extra_tag(b):   # fee value 1a00028181(off87..91) -> c2 1a00028181 (tag 2 bignum wrap)
    x=bytearray(b); assert x[87]==0x1a; x[87:87]=b"\xc2"; return bytes(x)
def m_isvalid_tag(b):     # is_valid f5(off199) -> c0 f5 (unexpected tag 0 wrap)
    x=bytearray(b); assert x[199]==0xf5; x[199:199]=b"\xc0"; return bytes(x)
def m_input_dup_set(b):   # inputs set: 81 -> 82, duplicate the single input element (non-canonical set: dup)
    x=bytearray(b); assert x[6]==0x81
    elem=bytes(x[7:43])  # 82 5820<32> 00  = 36 bytes
    x[6]=0x82; x[43:43]=elem; return bytes(x)
def m_fee_indef_str(b):   # (control-ish) trailing 2 bytes
    return b + b"\xf6\xf6"

MUTS=[
 ("00-base-control", lambda b:b, "both ACCEPT (sanity)"),
 ("01-trailing-1byte", m_trailing, "known finding: amaru accept / cardano decode-reject"),
 ("02-trailing-2byte", m_fee_indef_str, "trailing junk"),
 ("03-outer-array-indef", m_outer_indef, "indefinite outer array"),
 ("04-body-map-indef", m_body_indef, "indefinite body map (definite expected)"),
 ("05-witset-map-indef", m_wit_map_indef, "indefinite witness-set map (non-body)"),
 ("06-inputs-array-indef", m_inputs_arr_indef, "indefinite inputs array"),
 ("07-fee-key-nonminimal", m_fee_key_nonmin, "non-minimal uint for map key 2"),
 ("08-witmap-key-nonminimal", m_witkey_nonmin, "non-minimal uint for witness map key 0 (non-body)"),
 ("09-body-duplicate-key", m_dupkey_body, "duplicate map key (fee) in body"),
 ("10-fee-extra-tag", m_fee_extra_tag, "unexpected tag(2) wrapping fee value"),
 ("11-isvalid-extra-tag", m_isvalid_tag, "unexpected tag(0) wrapping is_valid (non-body)"),
 ("12-inputs-set-duplicate", m_input_dup_set, "non-canonical set: duplicate element"),
]

print(f"{'case':<26} {'amaru':<18} {'cardano':<18} verdict")
print("-"*90)
div=[]
for name,fn,note in MUTS:
    try: raw=fn(BASE)
    except AssertionError: print(f"{name:<26} OFFSET-ASSERT-FAIL"); continue
    os.system("/home/nigel/reset-pair.sh pair1 >/dev/null 2>&1")  # clean mempool per case
    time.sleep(1)
    ac,ab=submit(AM,raw); cc,cb=submit(CN,raw)
    acl=classify(ac,ab); ccl=classify(cc,cb)
    agree = (acl==ccl)
    verdict = "AGREE" if agree else "*** DIVERGENCE ***"
    print(f"{name:<26} {acl+'('+str(ac)+')':<18} {ccl+'('+str(cc)+')':<18} {verdict}")
    if not agree:
        div.append((name,note,acl,ac,ab[:200],ccl,cc,cb[:200]))
print()
if div:
    print("=== DIVERGENCES (full reason bodies) ===")
    for name,note,acl,ac,ab,ccl,cc,cb in div:
        print(f"\n### {name} — {note}")
        print(f"  amaru   [{acl} {ac}]: {ab}")
        print(f"  cardano [{ccl} {cc}]: {cb}")
else:
    print("No verdict-class divergence across mutations (all AGREE).")
