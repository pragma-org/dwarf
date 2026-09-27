use aiken/builtin
// operands via redeemer (runtime) so aiken cannot constant-fold -> forces the CEK interpreter to compute
validator divmod_divide {
  mint(r: List<Int>, _p: Data, _tx: Data) { expect [a, b, e] = r  builtin.divide_integer(a, b) == e }
}
validator divmod_quotient {
  mint(r: List<Int>, _p: Data, _tx: Data) { expect [a, b, e] = r  builtin.quotient_integer(a, b) == e }
}
validator divmod_mod {
  mint(r: List<Int>, _p: Data, _tx: Data) { expect [a, b, e] = r  builtin.mod_integer(a, b) == e }
}
validator divmod_remainder {
  mint(r: List<Int>, _p: Data, _tx: Data) { expect [a, b, e] = r  builtin.remainder_integer(a, b) == e }
}
