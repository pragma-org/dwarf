use aiken/builtin

// Error-path builtins. Edge value comes from the REDEEMER (runtime) so aiken cannot
// constant-fold the error at compile time. Each validator: valid redeemer -> accept,
// error redeemer -> the builtin errors -> script fails -> reject. Verdict-class parity.

validator arith_div {
  mint(r: Int, _p: Data, _tx: Data) {
    builtin.divide_integer(10, r) == 5        // r=2 -> 5 (accept); r=0 -> div-by-zero error
  }
}
validator arith_mod {
  mint(r: Int, _p: Data, _tx: Data) {
    builtin.mod_integer(10, r) == 0           // r=5 -> 0 (accept); r=0 -> mod-by-zero error
  }
}
validator cons_byte {
  mint(r: Int, _p: Data, _tx: Data) {
    builtin.cons_bytearray(r, #"") == #"41"   // r=65 -> 0x41 (accept); r=256 -> out-of-range error
  }
}
validator int_to_bytes_width {
  mint(r: Int, _p: Data, _tx: Data) {
    builtin.length_of_bytearray(builtin.integer_to_bytearray(True, r, 258)) > 0
    // r=0 -> minimal 2 bytes (accept); r=1 -> too small error; r=9000 -> oversized-width error
  }
}
validator bls_uncompress {
  mint(r: ByteArray, _p: Data, _tx: Data) {
    builtin.bls12_381_g1_compress(builtin.bls12_381_g1_uncompress(r)) == r
    // r=generator -> round-trips (accept); r=0xff*48 -> uncompress error (reject)
  }
}
