use aiken/builtin

// All inputs come from the REDEEMER (runtime Data) to avoid compile-time constant-folding of BLS
// points (aiken cannot flat-encode point constants). One validator per operation; vary the redeemer.

// uncompress a G1 point (redeemer = ByteArray). uncompress error -> script fails (reject/crash-hunt).
validator g1_uncompress { mint(r: Data,_p: Data,_t: Data){
  expect b: ByteArray = r
  builtin.length_of_bytearray(builtin.bls12_381_g1_compress(builtin.bls12_381_g1_uncompress(b)))==48 } }
// G1 canonical round-trip (redeemer = ByteArray): compress(uncompress(b))==b
validator g1_roundtrip { mint(r: Data,_p: Data,_t: Data){
  expect b: ByteArray = r
  builtin.bls12_381_g1_compress(builtin.bls12_381_g1_uncompress(b))==b } }
// uncompress a G2 point (redeemer = ByteArray)
validator g2_uncompress { mint(r: Data,_p: Data,_t: Data){
  expect b: ByteArray = r
  builtin.length_of_bytearray(builtin.bls12_381_g2_compress(builtin.bls12_381_g2_uncompress(b)))==96 } }
validator g2_roundtrip { mint(r: Data,_p: Data,_t: Data){
  expect b: ByteArray = r
  builtin.bls12_381_g2_compress(builtin.bls12_381_g2_uncompress(b))==b } }
// G1: 2*P == P+P  (redeemer = ByteArray P)
validator g1_scalarmul_double { mint(r: Data,_p: Data,_t: Data){
  expect b: ByteArray = r
  let p=builtin.bls12_381_g1_uncompress(b)
  builtin.bls12_381_g1_equal(builtin.bls12_381_g1_scalar_mul(2,p), builtin.bls12_381_g1_add(p,p)) } }
// G1: P + (-P) is identity (compresses to the infinity encoding, 48 bytes)  (redeemer = ByteArray P)
validator g1_add_neg { mint(r: Data,_p: Data,_t: Data){
  expect b: ByteArray = r
  let p=builtin.bls12_381_g1_uncompress(b)
  builtin.length_of_bytearray(builtin.bls12_381_g1_compress(builtin.bls12_381_g1_add(p, builtin.bls12_381_g1_neg(p))))==48 } }
// G2: 2*Q == Q+Q  (redeemer = ByteArray Q)
validator g2_scalarmul_double { mint(r: Data,_p: Data,_t: Data){
  expect b: ByteArray = r
  let q=builtin.bls12_381_g2_uncompress(b)
  builtin.bls12_381_g2_equal(builtin.bls12_381_g2_scalar_mul(2,q), builtin.bls12_381_g2_add(q,q)) } }
// Pairing bilinearity: e(5P,Q)==e(P,5Q). redeemer = List<ByteArray> [P_g1, Q_g2].
validator pairing_bilinear { mint(r: Data,_p: Data,_t: Data){
  expect [pb, qb]: List<ByteArray> = r
  let p=builtin.bls12_381_g1_uncompress(pb)
  let q=builtin.bls12_381_g2_uncompress(qb)
  builtin.bls12_381_final_verify(
    builtin.bls12_381_miller_loop(builtin.bls12_381_g1_scalar_mul(5,p), q),
    builtin.bls12_381_miller_loop(p, builtin.bls12_381_g2_scalar_mul(5,q))) } }
// Pairing TAMPERED: e(5P,Q) vs e(P,6Q) -> not equal -> finalVerify False -> reject (non-vacuity).
validator pairing_tampered { mint(r: Data,_p: Data,_t: Data){
  expect [pb, qb]: List<ByteArray> = r
  let p=builtin.bls12_381_g1_uncompress(pb)
  let q=builtin.bls12_381_g2_uncompress(qb)
  builtin.bls12_381_final_verify(
    builtin.bls12_381_miller_loop(builtin.bls12_381_g1_scalar_mul(5,p), q),
    builtin.bls12_381_miller_loop(p, builtin.bls12_381_g2_scalar_mul(6,q))) } }
// hashToGroup. redeemer = List<ByteArray> [msg, dst].
validator g1_hash_to_group { mint(r: Data,_p: Data,_t: Data){
  expect [msg, d]: List<ByteArray> = r
  builtin.length_of_bytearray(builtin.bls12_381_g1_compress(builtin.bls12_381_g1_hash_to_group(msg, d)))==48 } }
validator g2_hash_to_group { mint(r: Data,_p: Data,_t: Data){
  expect [msg, d]: List<ByteArray> = r
  builtin.length_of_bytearray(builtin.bls12_381_g2_compress(builtin.bls12_381_g2_hash_to_group(msg, d)))==96 } }
