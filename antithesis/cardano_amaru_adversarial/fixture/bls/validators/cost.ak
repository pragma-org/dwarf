use aiken/builtin
// Empty-bytestring memoryUsage amplifier: 20 appendByteString on empty operands. Each append feeds
// byte_string_ex_mem(empty) into the memory cost; amaru charges 1 per empty, cardano (formula) 0.
// redeemer = ByteArray (feed #"" to keep operands empty and avoid const-folding).
validator empty_bs_amp {
  mint(r: Data,_p: Data,_t: Data){
    expect b: ByteArray = r
    let a=builtin.append_bytearray(b,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    let a=builtin.append_bytearray(a,b)
    builtin.length_of_bytearray(a)==0
  }
}
