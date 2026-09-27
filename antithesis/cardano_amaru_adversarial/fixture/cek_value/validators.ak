use aiken/builtin

validator bignum_mul {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [a, b, e] = r
    builtin.multiply_integer(a, b) == e
  }
}

validator bignum_modmul {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [a, b, m, e] = r
    builtin.mod_integer(builtin.multiply_integer(a, b), m) == e
  }
}

validator bs_index {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [i, e] = r
    builtin.index_bytearray(#"00112233445566778899aabbccddeeff", i) == e
  }
}

validator bs_slice_len {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [s, l, el] = r
    builtin.length_of_bytearray(builtin.slice_bytearray(s, l, #"00112233445566778899aabbccddeeff")) == el
  }
}

validator i2b_b2i {
  mint(r: List<Int>, _p: Data, _tx: Data) {
    expect [en, w, n] = r
    let be = en == 1
    builtin.bytearray_to_integer(be, builtin.integer_to_bytearray(be, w, n)) == n
  }
}

validator nested_traverse {
  mint(d: Data, _p: Data, _tx: Data) {
    expect [l1] = builtin.un_list_data(d)
    expect [l2] = builtin.un_list_data(l1)
    expect [l3] = builtin.un_list_data(l2)
    expect [l4] = builtin.un_list_data(l3)
    expect [leaf] = builtin.un_list_data(l4)
    builtin.un_i_data(leaf) == 42
  }
}
