use aiken/builtin

// Each validator folds ONE builtin family many times over a redeemer-supplied
// List<Int> (so aiken cannot const-fold), and asserts an offline-computed
// expected result so the script ALWAYS passes phase-2 on a conformant VM.
// The only thing that can make it reject is the declared ex-unit budget being
// below the VM's metered cost -> the knife-edge cost differential.

// r = [expected, ..operands]

// ---- baseline: traverse the list, no builtin arithmetic ----
fn base(xs: List<Int>, n: Int) -> Int {
  when xs is {
    [] -> n
    [_, ..t] -> base(t, n)
  }
}

validator cost_baseline {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    base(xs, 0) == e
  }
}

// ---- addInteger (large magnitude accumulation) ----
fn addf(xs: List<Int>, acc: Int) -> Int {
  when xs is {
    [] -> acc
    [x, ..t] -> addf(t, builtin.add_integer(acc, x))
  }
}

validator cost_add {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    addf(xs, 0) == e
  }
}

// ---- multiplyInteger (bignum growth) ----
fn mulf(xs: List<Int>, acc: Int) -> Int {
  when xs is {
    [] -> acc
    [x, ..t] -> mulf(t, builtin.multiply_integer(acc, x))
  }
}

validator cost_mul {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    mulf(xs, 1) == e
  }
}

// ---- indexByteString (sum bytes at supplied indices) ----
fn idxf(xs: List<Int>, bs: ByteArray, acc: Int) -> Int {
  when xs is {
    [] -> acc
    [i, ..t] -> idxf(t, bs, builtin.add_integer(acc, builtin.index_bytearray(bs, i)))
  }
}

validator cost_index {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    idxf(xs, #"00112233445566778899aabbccddeeff", 0) == e
  }
}

// ---- consByteString (build a bytestring one byte at a time) ----
fn consf(xs: List<Int>, acc: ByteArray) -> ByteArray {
  when xs is {
    [] -> acc
    [b, ..t] -> consf(t, builtin.cons_bytearray(b, acc))
  }
}

validator cost_cons {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    builtin.length_of_bytearray(consf(xs, #"")) == e
  }
}

// ---- sliceByteString (fixed len 4, supplied starts; sum lengths) ----
fn slicef(xs: List<Int>, bs: ByteArray, acc: Int) -> Int {
  when xs is {
    [] -> acc
    [s, ..t] ->
      slicef(t, bs, builtin.add_integer(acc, builtin.length_of_bytearray(builtin.slice_bytearray(s, 4, bs))))
  }
}

validator cost_slice {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    slicef(xs, #"00112233445566778899aabbccddeeff", 0) == e
  }
}

// ---- serialiseData (fold: sum serialised lengths of iData(x)) ----
fn serf(xs: List<Int>, acc: Int) -> Int {
  when xs is {
    [] -> acc
    [x, ..t] -> serf(t, builtin.add_integer(acc, builtin.length_of_bytearray(builtin.serialise_data(builtin.i_data(x)))))
  }
}

validator cost_serialise {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    serf(xs, 0) == e
  }
}

// ---- equalsData (fold: count equalsData(iData(x), iData(x)) trues) ----
fn eqf(xs: List<Int>, acc: Int) -> Int {
  when xs is {
    [] -> acc
    [x, ..t] ->
      eqf(t, if builtin.equals_data(builtin.i_data(x), builtin.i_data(x)) { builtin.add_integer(acc, 1) } else { acc })
  }
}

validator cost_equalsdata {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    eqf(xs, 0) == e
  }
}

// ---- blake2b_256 (iterated hashing: h = blake2b_256(h), |xs| times) ----
fn hb(xs: List<Int>, acc: ByteArray) -> ByteArray {
  when xs is {
    [] -> acc
    [_, ..t] -> hb(t, builtin.blake2b_256(acc))
  }
}

validator cost_blake2b {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    builtin.length_of_bytearray(hb(xs, #"00")) == e
  }
}

// ---- sha2_256 (iterated) ----
fn hs(xs: List<Int>, acc: ByteArray) -> ByteArray {
  when xs is {
    [] -> acc
    [_, ..t] -> hs(t, builtin.sha2_256(acc))
  }
}

validator cost_sha2 {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    builtin.length_of_bytearray(hs(xs, #"00")) == e
  }
}

// ---- keccak_256 (iterated) ----
fn hk(xs: List<Int>, acc: ByteArray) -> ByteArray {
  when xs is {
    [] -> acc
    [_, ..t] -> hk(t, builtin.keccak_256(acc))
  }
}

validator cost_keccak {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [e, ..xs] = r
    builtin.length_of_bytearray(hk(xs, #"00")) == e
  }
}
