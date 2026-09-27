use aiken/builtin
// append_string-dominated (20 append, single trailing equals). Isolates append_string cost.
validator app_only {
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
    let x=builtin.append_string(x,s)
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
