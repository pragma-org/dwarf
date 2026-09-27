use aiken/builtin
// String memoryUsage: decode_utf8(redeemer) -> runtime String (avoids const-fold), append 20x, equal.
validator string_amp {
  mint(r: Data,_p: Data,_t: Data){
    expect b: ByteArray = r
    let s=builtin.decode_utf8(b)
    let x=builtin.append_string(s,s)
    let x=builtin.append_string(x,s)
    let x=builtin.append_string(x,s)
    let x=builtin.append_string(x,s)
    let x=builtin.append_string(x,s)
    let x=builtin.append_string(x,s)
    let x=builtin.append_string(x,s)
    let x=builtin.append_string(x,s)
    let x=builtin.append_string(x,s)
    let x=builtin.append_string(x,s)
    builtin.equals_string(x,x)
  }
}
