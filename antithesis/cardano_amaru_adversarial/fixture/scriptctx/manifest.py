import hashlib, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
CASES = {
 "ctxfee-ok":("accept",True,"assert txInfoFee == 2000000 -> accept"),
 "ctxfee-wrong":("reject",False,"assert txInfoFee == 2000001 -> reject"),
 "ctxmint-ok":("accept",True,"assert txInfoMint qty of own COLL == 1 -> accept"),
 "ctxmint-wrong":("reject",False,"assert qty == 2 -> reject"),
 "ctxnuminputs-ok":("accept",True,"assert length txInfoInputs == 1 -> accept"),
 "ctxnuminputs-wrong":("reject",False,"assert == 2 -> reject"),
 "ctxnumref-ok":("accept",True,"1 ref input; assert length txInfoReferenceInputs == 1 -> accept"),
 "ctxnumref-wrong":("reject",False,"1 ref input; assert == 0 -> reject"),
 "ctxnumref-none-ok":("accept",True,"no ref input; assert length == 0 -> accept"),
 "ctxinputtxid-ok":("accept",True,"assert first txInfoInputs output_reference txid == funding txid -> accept (inputs content fidelity)"),
 "ctxinputtxid-wrong":("reject",False,"assert first input txid == a different txid -> reject"),
 "ctxredeemers-ok":("accept",True,"assert length txInfoRedeemers == 1 -> accept"),
 "ctxredeemers-wrong":("reject",False,"assert == 2 -> reject"),
 "ctxtreasury-ok":("accept",True,"CONWAY: --treasury-donation 5; assert tx.treasury_donation == Some(5) -> accept"),
 "ctxtreasury-wrong":("reject",False,"CONWAY: donation 5; assert == Some(6) -> reject"),
 "ctxtreasuryamt-none-ok":("accept",True,"CONWAY: assert tx.current_treasury_amount == None -> accept"),
}
cases=[]
for cid,(exp,single,note) in CASES.items():
    f=HERE/f"{cid}.tx"
    if not f.exists(): continue
    cbor=bytes.fromhex(json.loads(f.read_text())["cborHex"])
    cases.append({"case_id":cid,"tx_file":f"{cid}.tx","expected":exp,"single_use":single,
                  "cbor_size":len(cbor),"cbor_sha256":hashlib.sha256(cbor).hexdigest(),"note":note})
(HERE/"plutus_corpus.json").write_text(json.dumps({"description":"ScriptContext (Conway TxInfo) construction fidelity (cardano 11.1.2 vs amaru 0925). A minting policy asserts a TxInfo field == a redeemer/tx-supplied expectation; correct-value accepts, wrong-expectation rejects, on both. One-accepts-one-rejects = a HIGH is_valid consensus divergence. Fields: fee, mint, inputs (count+content), reference_inputs (count+presence), redeemers, and Conway treasury_donation / current_treasury_amount.","cases":cases},indent=1)+"\n")
print(f"wrote {len(cases)} cases")
