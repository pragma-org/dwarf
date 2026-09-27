#!/usr/bin/env python3
import json, subprocess, os, time, urllib.request, urllib.error
D="/tmp/bls-aiken"; os.chdir(D)
AIKEN=os.path.expanduser("~/.aiken/bin/aiken")
IMG="ghcr.io/intersectmbo/cardano-node:11.1.2"
uid=os.getuid(); gid=os.getgid()
CLI=["docker","run","--rm","-u",f"{uid}:{gid}","-v",f"{D}:/w","-w","/w","--entrypoint","cardano-cli",IMG]
IN="9708b921e619b34a6fafc375ebd46bf65d77eed427f953eabed2b9da9212c4a1#0"
ADDR="addr_test1vrj6thds8l3hqktz0lk7w9zcgq0qmktmkdcm80tuecxjxfcjg8ye6"
TOTAL=200000000000000; FEE=3000000; EXU="(10000000000,10000000)"; M=["--testnet-magic","42"]
AM="http://localhost:3210/api/submit/tx"; CN="http://localhost:8110/api/submit/tx"
G1="97f1d3a73197d7942695638c4fa9ac0fc3688c4f9774b905a14e3a3f171bac586c55e83ff97a1aeffb3af00adb22c6bb"
G2="93e02b6052719f607dacd3a088274f65596bd0d09920b61ab5da61bbdc7f5049334cf11213945d57e5ac7d055d042b7e024aa2b2f08f0a91260805272dc51051c6e47ad4fa403b02b4510b647ae3d1770bac0326a805bbefd48056c8c121bdb8"
DST="424c535f5349475f424c53313233383147325f584d443a5348412d3235365f535357555f524f5f4e554c5f"
def by(h): return {"bytes":h}
def lst(*hs): return {"list":[{"bytes":h} for h in hs]}
# (case_id, validator, redeemer_obj, expected)  expected: accept|reject
CASES=[
 ("g1-uncompress-valid","g1_uncompress",by(G1),"accept"),
 ("g1-uncompress-short","g1_uncompress",by(G1[:-2]),"reject"),
 ("g1-uncompress-long","g1_uncompress",by(G1+"aa"),"reject"),
 ("g1-uncompress-allzero","g1_uncompress",by("00"*48),"reject"),
 ("g1-uncompress-garbage","g1_uncompress",by("ff"*48),"reject"),
 ("g1-uncompress-infinity","g1_uncompress",by("c0"+"00"*47),"accept"),
 ("g1-uncompress-badflag","g1_uncompress",by("17"+G1[2:]),"reject"),
 ("g1-roundtrip-valid","g1_roundtrip",by(G1),"accept"),
 ("g2-uncompress-valid","g2_uncompress",by(G2),"accept"),
 ("g2-uncompress-short","g2_uncompress",by(G2[:-2]),"reject"),
 ("g2-uncompress-garbage","g2_uncompress",by("ff"*96),"reject"),
 ("g2-uncompress-allzero","g2_uncompress",by("00"*96),"reject"),
 ("g2-uncompress-infinity","g2_uncompress",by("c0"+"00"*95),"accept"),
 ("g1-scalarmul-double","g1_scalarmul_double",by(G1),"accept"),
 ("g1-add-neg","g1_add_neg",by(G1),"accept"),
 ("g2-scalarmul-double","g2_scalarmul_double",by(G2),"accept"),
 ("pairing-bilinear","pairing_bilinear",lst(G1,G2),"accept"),
 ("pairing-tampered","pairing_tampered",lst(G1,G2),"reject"),
 ("g1-hash-to-group","g1_hash_to_group",lst("deadbeef",DST),"accept"),
 ("g2-hash-to-group","g2_hash_to_group",lst("deadbeef",DST),"accept"),
 ("g1-hash-emptydst","g1_hash_to_group",lst("deadbeef",""),"reject"),
]
def sh(c): return subprocess.run(c,capture_output=True,text=True)
def convert(v):
    f=f"{D}/{v}.plutus"
    if not os.path.exists(f):
        r=sh([AIKEN,"blueprint","convert","-m","bls","-v",v])
        open(f,"w").write(r.stdout)
    return f
def policyid(pl): return sh(CLI+["conway","transaction","policyid","--script-file",f"/w/{os.path.basename(pl)}"]).stdout.strip()
def build(cid,v,red):
    pl=convert(v); json.dump(red,open(f"{D}/{cid}.redeemer.json","w"))
    pol=policyid(pl); tok=f"1 {pol}.434f4c4c"
    r=sh(CLI+["conway","transaction","build-raw","--script-valid","--tx-in",IN,"--tx-in-collateral",IN,
        "--tx-out",f"{ADDR}+{TOTAL-FEE}+{tok}","--fee",str(FEE),"--mint",tok,
        "--mint-script-file",f"/w/{v}.plutus","--mint-redeemer-file",f"/w/{cid}.redeemer.json",
        "--mint-execution-units",EXU,"--protocol-params-file","/w/pparams.json","--out-file",f"/w/{cid}.raw"])
    if r.returncode!=0: return None, r.stderr[-300:]
    s=sh(CLI+["conway","transaction","sign","--tx-body-file",f"/w/{cid}.raw","--signing-key-file","/w/payment.skey"]+M+["--out-file",f"/w/{cid}.tx"])
    if s.returncode!=0: return None, s.stderr[-300:]
    return json.load(open(f"{D}/{cid}.tx"))["cborHex"], None
def submit(url,hexs):
    raw=bytes.fromhex(hexs)
    q=urllib.request.Request(url,data=raw,headers={"Content-Type":"application/cbor"},method="POST")
    try:
        x=urllib.request.urlopen(q,timeout=30); return x.status, x.read().decode("utf8","replace")
    except urllib.error.HTTPError as e: return e.code, e.read().decode("utf8","replace")
    except Exception as e: return 0, "ERR:"+str(e)
def alive(url):
    try:
        q=urllib.request.Request(url,data=b"\x00",headers={"Content-Type":"application/cbor"},method="POST")
        x=urllib.request.urlopen(q,timeout=8); return x.status
    except urllib.error.HTTPError as e: return e.code
    except Exception: return 0
def cls(code,body):
    if code==202: return "ACCEPT"
    if code==0: return "NO_RESP/CRASH?"
    b=body.lower()
    if any(s in b for s in ["tag mismatch","validationtagmismatch","failed phase two","failedunexpectedly","scripts to pass but they fail","passedunexpectedly"]): return "PHASE2_REJECT"
    if any(s in b for s in ["deserialise","decode"]): return "DECODE_REJECT"
    return f"REJECT({code})"
print(f"{'case':<26}{'exp':<8}{'amaru':<18}{'cardano':<18}{'alive':<7}verdict")
print("-"*95)
div=[]
for cid,v,red,exp in CASES:
    hexs,err=build(cid,v,red)
    if hexs is None: print(f"{cid:<26}{exp:<8}BUILD_FAIL: {err}"); continue
    os.system("/home/nigel/reset-pair.sh pair1 >/dev/null 2>&1"); time.sleep(1)
    ac,ab=submit(AM,hexs); cc,cb=submit(CN,hexs)
    al=alive(AM)
    acl=cls(ac,ab); ccl=cls(cc,cb)
    agree=(acl==ccl); crash=(al!=400)
    v_=("AGREE" if agree else "*** DIVERGENCE ***")+(" !!!AMARU-CRASH!!!" if crash else "")
    print(f"{cid:<26}{exp:<8}{acl+'('+str(ac)+')':<18}{ccl+'('+str(cc)+')':<18}{str(al):<7}{v_}")
    if not agree or crash: div.append((cid,acl,ac,ab[:220],ccl,cc,cb[:220],al))
print()
for cid,acl,ac,ab,ccl,cc,cb,al in div:
    print(f"### {cid} (amaru_alive={al})\n  amaru [{acl} {ac}]: {ab}\n  cardano [{ccl} {cc}]: {cb}\n")
if not div: print("No divergence, no crash (all AGREE).")
