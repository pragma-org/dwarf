// L1: three DISTINCT always-succeeds PlutusV3 minting policies (distinct policy ids => 3 distinct
// Mint redeemer keys). Each succeeds iff the redeemer int equals its own tag; we always pass the
// matching tag, so each script trivially completes far under its (huge, edited) declared budget.
validator m1 { mint(r: Data, _p: Data, _t: Data) { expect n: Int = r
  n == 1 } }
validator m2 { mint(r: Data, _p: Data, _t: Data) { expect n: Int = r
  n == 2 } }
validator m3 { mint(r: Data, _p: Data, _t: Data) { expect n: Int = r
  n == 3 } }
