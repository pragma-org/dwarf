use aiken/builtin

// redeemer = [vk, msg, sig]; assert the ed25519 verification result
validator ed25519 {
  mint(r: List<ByteArray>, _p: Data, _tx: Data) {
    expect [vk, msg, sig] = r
    builtin.verify_ed25519_signature(vk, msg, sig)
  }
}
validator ecdsa {
  mint(r: List<ByteArray>, _p: Data, _tx: Data) {
    expect [vk, msg, sig] = r
    builtin.verify_ecdsa_secp256k1_signature(vk, msg, sig)
  }
}
validator schnorr {
  mint(r: List<ByteArray>, _p: Data, _tx: Data) {
    expect [vk, msg, sig] = r
    builtin.verify_schnorr_secp256k1_signature(vk, msg, sig)
  }
}
// redeemer = [input, expected]; assert hash(input) == expected
validator h_blake2b_256 { mint(r: List<ByteArray>, _p: Data, _tx: Data) { expect [i, e] = r  builtin.blake2b_256(i) == e } }
validator h_blake2b_224 { mint(r: List<ByteArray>, _p: Data, _tx: Data) { expect [i, e] = r  builtin.blake2b_224(i) == e } }
validator h_keccak_256  { mint(r: List<ByteArray>, _p: Data, _tx: Data) { expect [i, e] = r  builtin.keccak_256(i) == e } }
validator h_sha2_256    { mint(r: List<ByteArray>, _p: Data, _tx: Data) { expect [i, e] = r  builtin.sha2_256(i) == e } }
validator h_sha3_256    { mint(r: List<ByteArray>, _p: Data, _tx: Data) { expect [i, e] = r  builtin.sha3_256(i) == e } }
validator h_ripemd_160  { mint(r: List<ByteArray>, _p: Data, _tx: Data) { expect [i, e] = r  builtin.ripemd_160(i) == e } }
