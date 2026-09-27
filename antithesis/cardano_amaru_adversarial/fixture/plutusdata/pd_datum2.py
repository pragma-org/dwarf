import json,io
import cbor2
P="/tmp/nsprobe/pd"
tx=bytes.fromhex(json.load(open(f"{P}/spendb-canon.tx"))["cborHex"])
s=io.BytesIO(tx[1:]);d=cbor2.CBORDecoder(s);sp=[]
for _ in range(4):
    a=s.tell();d.decode();b=s.tell();sp.append((1+a,1+b))
body,wits,tail=tx[sp[0][0]:sp[0][1]],tx[sp[1][0]:sp[1][1]],tx[sp[2][0]:sp[3][1]]
wd=cbor2.loads(wits)
print("witness keys:",sorted(wd.keys()),"| datums key4:",wd.get(4))
CANON=bytes.fromhex("d87980")
print("d87980 count in wits:",wits.count(CANON))
# edit the LAST occurrence (datum) to indefinite; if redeemer also d87980, both are unit-mint/spend
i=wits.find(CANON)
nw=wits[:i]+bytes.fromhex("d8799fff")+wits[i+3:]
ntx=b"\x84"+body+nw+tail
json.dump({"type":"Tx ConwayEra","description":"spendB datum-hash UTxO, non-canonical (indefinite) supplied datum","cborHex":ntx.hex()},open(f"{P}/pd-datum-noncanon.tx","w"))
print("wrote pd-datum-noncanon.tx (edited FIRST(datum) at offset",i,")")
