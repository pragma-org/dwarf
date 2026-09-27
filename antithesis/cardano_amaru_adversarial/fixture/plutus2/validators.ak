use aiken/builtin
use aiken/interval.{Finite}
use cardano/transaction.{Transaction}

// (1) integerToByteString (V3): big-endian, width 4, 258 -> 0x00000102.
validator int_to_bytes {
  mint(_r: Data, _p: Data, _tx: Data) {
    builtin.integer_to_bytearray(True, 4, 258) == #"00000102"
  }
}

// (2) BLS12-381 G1: uncompress+compress the canonical generator must round-trip.
validator bls_roundtrip {
  mint(_r: Data, _p: Data, _tx: Data) {
    let g = #"97f1d3a73197d7942695638c4fa9ac0fc3688c4f9774b905a14e3a3f171bac586c55e83ff97a1aeffb3af00adb22c6bb"
    builtin.bls12_381_g1_compress(builtin.bls12_381_g1_uncompress(g)) == g
  }
}

// (3) ScriptContext construction: VM must build txInfoValidRange lower bound = POSIXTime for the
// tx invalidBefore slot. Raw mapping agrees (same systemStart+slots), isolating VM construction.
// invalidBefore=1000 -> 1790492288000 ms.
validator ctx_validity_lower {
  mint(_r: Data, _p: Data, tx: Transaction) {
    expect Finite(lo) = tx.validity_range.lower_bound.bound_type
    lo == 1790492288000
  }
}

// negative controls (assertion FALSE -> script fails -> both must reject; proves non-vacuity).
validator int_to_bytes_wrong {
  mint(_r: Data, _p: Data, _tx: Data) {
    builtin.integer_to_bytearray(True, 4, 258) == #"00000103"
  }
}
validator bls_wrong {
  mint(_r: Data, _p: Data, _tx: Data) {
    let g = #"97f1d3a73197d7942695638c4fa9ac0fc3688c4f9774b905a14e3a3f171bac586c55e83ff97a1aeffb3af00adb22c6bb"
    builtin.bls12_381_g1_compress(builtin.bls12_381_g1_uncompress(g)) == #"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
}
validator ctx_validity_wrong {
  mint(_r: Data, _p: Data, tx: Transaction) {
    expect Finite(lo) = tx.validity_range.lower_bound.bound_type
    lo == 1790492288001
  }
}
