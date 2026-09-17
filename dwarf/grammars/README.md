# DWARF Generation Grammars and Mutation Tokens

This tree holds validated, machine-readable descriptions of fuzz-input shapes
and byte-token mutation hints retained for the M2 serialization/deserialization
seed material. These files are generation assets, not the human glossary.

## Layout

- `dwarf/grammars/<target>/dict.txt`
  - finite, quoted libFuzzer dictionary entries for one fuzz target
  - entries are quoted byte strings using libFuzzer dictionary syntax
- `dwarf/grammars/<target>/structure.json`
  - validates against `dwarf/spec/v1/generation-structure.schema.json`
  - records the input format, target, real decoder entrypoint, and outer shape
  - descriptive metadata today, not an executable generator or state machine

## Current Plumbing

The retained dictionaries use the layout expected by
`dwarf/scripts/cargo_fuzz_campaign.py`:

- `dwarf/grammars/<fuzz-dir-name>/dict.txt`

For a shipped asset, `cargo_fuzz_campaign.py` auto-discovers `dict.txt` only
when the grammar directory name exactly matches the selected fuzz-directory
basename. Its libFuzzer invocation then appends:

- `-dict=<path>`

to `cargo fuzz run ... -- ...`.

A dashboard runtime clone does not replace or shadow that shipped asset. It is
inert until it is selected explicitly with `cargo_fuzz_campaign.py --dict-path`
or with the `dict_path` parameter of a supported DWARF runtime campaign
primitive, such as `runtime_custom_mutator_template` or
`runtime_aflpp_campaign`. The selected engine still determines whether the
dictionary syntax is compatible; selection does not make the engines
interchangeable.

The engines are deliberately not conflated:

- `cargo-fuzz` normally drives Rust targets through libFuzzer.
- AFL++ is a separate coverage-guided engine used by other DWARF harness paths.
- AFLNet is a separate state-aware network-fuzzing path.
- CDDL describes valid CBOR; Cuddle can validate CDDL and generate valid seed
  bytes outside the live fuzz loop.

A compatible-looking corpus or dictionary does not make those engines
interchangeable. A `dict.txt` token set is not a protocol transcript, an agency
model, or a session-state machine.

## Scope

These assets do not change decoder behavior, inject code into a node, or change
replay semantics. `structure.json` documents the target envelope. `dict.txt`
biases byte mutation toward useful CBOR prefixes, message identifiers, and
constants. The real target and its harness still determine what code executes.

Shipped definitions are immutable in the dashboard. Operators may clone an
exact source pair into the explicitly configured runtime grammar root, then use
the structured or raw editor. Both files must pass schema and finite token-line
validation before a runtime save; repository files are never rewritten.

## Coverage

Retained M2 grammar coverage includes:

- single-implementation parser and mini-protocol seed families
  - block
  - handshake
  - chainsync
  - blockfetch
  - txsubmission

Each `structure.json` is intentionally lightweight. It records the outer case
shape, common nested field forms, and decoder entrypoint so future
custom-mutator work has a stable starting point without claiming visibility or
behavior the current harnesses do not provide.
