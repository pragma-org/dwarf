"""Write refscript_corpus.json from the 9 spend txs refscript_build.sh produced."""
import hashlib, json, os
from pathlib import Path
HERE = Path(__file__).resolve().parent
CASES = {
 "spendA-witness":("accept",True,"spend inline-datum script UTxO, script in WITNESS -> accept (baseline)"),
 "spendA-refscript":("accept",True,"same spend, script via REFERENCE (C_ok) -> accept; MUST equal spendA-witness (reference-script resolution)"),
 "spendB-hash-witness":("accept",True,"spend datum-HASH UTxO, datum SUPPLIED, script in witness -> accept"),
 "spendB-refscript":("accept",True,"spend datum-HASH UTxO via reference script, datum supplied -> accept"),
 "spendB-missing-datum":("reject",False,"spend datum-HASH UTxO with datum OMITTED -> both reject (missing datum)"),
 "spendD-fail-valid":("reject",False,"spend always-FAILS UTxO, is_valid=true -> script fails -> both reject (tag mismatch)"),
 "spendD-fail-invalid":("accept",True,"spend always-FAILS UTxO, is_valid=false -> claim matches -> both accept (collateral consumed)"),
 "spendA-wrong-refscript":("reject",False,"spend always-succeeds UTxO but provide C_fail as the reference script (hash != addr) -> both reject (script mismatch); must NOT run the wrong script"),
 "spendA-refscript-also-refinput":("accept",True,"C_ok used as spending reference script AND as a read-only reference input -> both accept (dedup/disjointness edge)"),
}
cases=[]
for cid,(exp,single,note) in CASES.items():
    f=HERE/f"{cid}.tx"
    if not f.exists():  # allow partial (minimal-cut) builds
        continue
    cbor=bytes.fromhex(json.loads(f.read_text())["cborHex"])
    cases.append({"case_id":cid,"tx_file":f"{cid}.tx","expected":exp,"single_use":single,
                  "cbor_size":len(cbor),"cbor_sha256":hashlib.sha256(cbor).hexdigest(),"note":note})
(HERE/"refscript_corpus.json").write_text(json.dumps({"description":"Reference-script + inline-datum phase-2 differential (cardano 11.1.2 vs amaru 0925). Spends pre-mined script UTxOs; reference-script resolution (witness vs reference must agree), inline-vs-datum-hash, script-fail is_valid paths, wrong reference script. Verdict + reason-class parity; fail-closed.","cases":cases},indent=1)+"\n")
print(f"wrote {len(cases)} cases")
