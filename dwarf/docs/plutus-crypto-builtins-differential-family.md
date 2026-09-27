# Plutus VM crypto builtins — signature-verify + hash differential family

The highest-impact untested VM area: a divergence in a cryptographic builtin = a signature/hash
that validates on one node but not the other = phase-2 consensus split **and** an authorization
bypass. PlutusV3 aiken validators call each builtin and assert its result against a
redeemer-supplied expectation; correct-input accepts, wrong-input rejects, on both. Driver
`workload/plutus_differential.py`. (BLS12-381 builtins are a sibling lane, dwarf-v4-fd.)

> **STATUS (2026-09-27): 16/16 AGREE, no divergence, no builtin crash.** Amaru v10.11.20260925
> (`eaf8ac3f`) matches cardano-node 11.1.2 (`fef83fed`) on `verifyEd25519Signature` and the hash
> builtins. Graded on pair4; evidence `fixture/crypto_builtins/graded-2026-09-27.json`.

## verifyEd25519Signature

| case | input | expected | result |
|---|---|---|---|
| ed25519-valid | a real (vk, msg, sig) triple | accept | AGREE (both accept, same tx id) |
| ed25519-wrongsig | sig byte flipped | reject | AGREE (verify False) |
| ed25519-wrongmsg | msg byte flipped | reject | AGREE (verify False) |
| **ed25519-noncurve-vk** | vk = `0x0200…00` (a **non-curve-point**, the finding-#1 crash class) fed INTO the builtin | reject | **AGREE — both reject gracefully; amaru does NOT crash (stayed live)** |

**Key negative cross-ref:** finding #1 (`finding-amaru-vkey-noncurve-point-crash.md`) is a
ledger-thread panic when a non-curve-point key reaches the tx-**witness** verifier's `.expect()`.
Feeding the same class of key *into the VM builtin* `verifyEd25519Signature` does **not** crash
Amaru — it fails gracefully (phase-2 script failure), identically to cardano-node. So that panic is
specific to the witness-path `.expect()`, not the shared ed25519 builtin.

## Hash builtins (input "abc", asserted against known vectors)

| builtin | ok (accept) | wrong (reject) | result |
|---|---|---|---|
| blake2b_256 | correct vector | flipped | AGREE |
| blake2b_224 | correct vector | flipped | AGREE |
| keccak_256 | correct vector (true Ethereum keccak, **not** SHA3) | flipped | AGREE |
| sha2_256 | correct vector | flipped | AGREE |
| sha3_256 | correct vector | flipped | AGREE |
| ripemd_160 | — | — | AGREE (**both reject**, see below) |

Five hash builtins compute identically to the standard on both nodes (correct vector accepts,
wrong rejects). **ripemd_160 is uncosted in this chain's PlutusV3 cost model** — Amaru's error
shows `consumed_budget` = the int64-max sentinel, i.e. no cost entry, so any invocation exceeds the
transaction budget and the script fails; both nodes reject it identically. Its output correctness
is therefore not reachable on this chain (cost-model-limited), but the behaviour is conformant.

## Method & boundary

A valid tx carries cardano-cli's `script_data_hash`; each validator is a mint policy whose redeemer
carries the builtin inputs. keccak_256's reference was computed with a pure-Python keccak (verified
against the known `keccak256("")` vector), since it differs from SHA3. `verifyEcdsaSecp256k1Signature`
/ `verifySchnorrSecp256k1Signature` and BLS12-381 are follow-ups (secp256k1/BIP340 test vectors;
BLS is dwarf-v4-fd's lane).

## Reproduce

```
# aiken validators in fixture/crypto_builtins/validators.ak (module crypto); redeemer = [inputs...]
/home/nigel/reset-pair.sh pair4
cd workload && python3 plutus_differential.py --corpus ../fixture/crypto_builtins --amaru :3213 --cardano :8113
python3 plutus_differential.py --corpus ../fixture/crypto_builtins --single ed25519-valid …   # accepts, per reset
```
Exit 0 all agree / 1 divergence / 2 inconclusive. Keys testnet-only.
