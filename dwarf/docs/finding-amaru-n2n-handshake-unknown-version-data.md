# Amaru drops the whole node-to-node handshake when an offer contains a version it does not know (Cardano-node 11.1 NodeToNodeV_16)

**Component:** `amaru-protocols` handshake version-table decoding (`crates/amaru-protocols/src/protocol_messages/version_table.rs`, `version_data.rs`, `version_number.rs`)
**Type:** Node-to-node interoperability / protocol forward-compatibility divergence vs. the Haskell reference node
**Status:** Confirmed (root cause in source; reproduced as a retained DWARF run with SARIF and patched measurement evidence). **Present in the latest release `v10.11.20260918` and on `main` (`5a2a08bc`, 2026-09-22).** No upstream issue exists (Amaru issues/PRs searched for handshake/V16/Peras/version-data; the related #1304, #1319 and #1333 fixed negotiation and V15, not unknown-version tolerance).
**Found by:** DWARF version qualification of the mixed pair Cardano-node 11.1.2 + Amaru 10.11.20260912 (classified *incompatible* on 2026-09-18 with no assigned cause), root-caused and reproduced by DWARF on 2026-09-23.
**Date:** 2026-09-23
**Severity:** Medium (interoperability / availability; latent network-wide break at the next protocol-version rollout; not consensus-affecting, no crash — see *Severity*).

---

## Summary

A node-to-node handshake starts with `MsgProposeVersions`, a map from version number to
that version's parameters. A responder must pick the greatest version both sides support and
ignore versions it does not know.

Amaru instead decodes **every** entry of the offered table with its own V11–V15 parameter
layout (a 4-element array). If the initiator offers any version whose parameters have a
different shape, the whole `MsgProposeVersions` fails to decode and Amaru closes the
connection without replying (no `MsgAcceptVersion`, no `MsgRefuse`).

Cardano-node 11.1.x offers exactly such a version when `ExperimentalProtocolsEnabled` is set:
`NodeToNodeV_16` (Peras) appends a fifth field, `perasSupport`. As a result, **no
Cardano-node 11.1+ with experimental protocols enabled can connect to an Amaru responder**,
and the same will happen to every peer once V16 (or any later version with new parameters)
is offered by default. The Haskell node answers the same offers with the greatest common
version and ignores version numbers it does not know.

## Environment

| | |
|---|---|
| Amaru (target) | `v10.11.20260918`, source `aedfe797a5b8ef00d8b362be40b47a52c3b4a379`; stock image `ghcr.io/pragma-org/amaru@sha256:2176f12085219a4052be17dca89742954897eb9a5321554c799d0458913c9c60`; DWARF measurement build `dwarf/amaru-measurement@sha256:b3f6c0cec64c62e0500f012367d1661872efede9b683c8a56dd870a42c8fb3fd` (nanoseconds-v3 instrumentation, patch set `47787f15…`) |
| Amaru (also affected) | `main` `5a2a08bc` (decoder byte-identical); `v10.11.20260912` (DWARF mixed qualification 2026-09-18); `v10.11.20260903` (live relay, 2026-09-23) |
| Reference | Cardano-node `11.1.2` (`fef83fed01d7926f3de83b3b917be5a4a48768b5`), DWARF measurement target `dwarf/cardano-measurement@sha256:956ae21c…` on profile `profile-v-cardano-measurement-nanoseconds-v2`; stock `ghcr.io/intersectmbo/cardano-node@sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f` in the mixed devnet |
| Network | DWARF devnets `testnet_42`: mixed profile `profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3` (2× Cardano-node 11.1.2 producers + Amaru relays + Amaru-fed Cardano consumer) for the Amaru leg; `profile-v-cardano-measurement-nanoseconds-v2` for the reference leg |
| Workload | DWARF case set `version-table-forward-compat-v1`, 60 raw `MsgProposeVersions` attempts (20 per case) to Amaru N2N port 3000 |

## Observed behaviour

DWARF run `20260923T145318Z-508a12bd` (scenario `amaru-n2n-handshake-version-table-forward-compat-20260918-mixed-1112`),
classified by DWARF as `completed_with_security_finding` / `amaru-n2n-handshake-unknown-version-data`;
reference run `20260923T150024Z-5ac32f61` (scenario `cardano-n2n-handshake-version-table-forward-compat-1112`, pass):

| Offer (payload) | Amaru 10.11.20260918 | Cardano-node 11.1.2 (reference) |
|---|---|---|
| V14 + V15 (`8200a20e84182af401f40f84182af401f4`) | 20/20 **accepted** (V15) | 20/20 **accepted** (V15) |
| V14 + V15 + **V16 with perasSupport** — the exact Cardano-node 11.1.x experimental offer (`…1085182af401f4f4`) | 20/20 **rejected — connection closed, no reply** | 20/20 **accepted** (V16 — this node supports it) |
| V14 + V15 + unknown future version 99 (`…186382182a66667574757265`) | 20/20 **rejected — connection closed, no reply** | 20/20 **accepted** (V15 — version 99 ignored) |

Amaru's own patched instrumentation (`amaru-patched-protocol-decode`, same run) shows where it
fails: all 60 frames were **framed** at the mux, 20 **decoded**, and **40 classified
`malformed` by the handshake message decoder** with negotiation **`not_attempted`**. The node
stayed up (no restart, no fatal signal); only the connection is dropped.

End-to-end with a real initiator: a Cardano-node 11.1.2 whose only upstream was a live
Amaru `v10.11.20260903` relay failed every connection attempt with `BearerClosed` during the
handshake when `ExperimentalProtocolsEnabled=true`, and completed the handshake and downloaded
headers with `false` (manual A/B during root-causing, 2026-09-23; not a retained DWARF run).
DWARF's mixed qualification shows the same at topology level: Cardano-node 11.1.2 +
Amaru `10.11.20260912` was classified *incompatible* with the flag on (qualification
`20260918T092619Z…89b35bbd`: the Amaru-fed consumer stayed at its seed slot while Amaru logged
`connection.child_died child="Handshake"`), and Cardano-node 11.1.2 + Amaru `10.11.20260918`
passed every mixed gate with the flag pinned off (qualification `20260923T140419Z…867a6f18`).

## Root cause

`version_table.rs` decodes the value of **every** offered version with that version as context:

```rust
for _ in 0..len {
    let key = d.decode()?;
    let mut ctx = key;
    let value = d.decode_with(&mut ctx)?;   // any failure aborts the whole table
    values.insert(key, value);
}
```

`version_data.rs` then requires the V11–V15 layout for any version ≥ 11:

```rust
if ctx.as_ref().has_query_and_peer_sharing() {        // version_number.rs: self.0 >= 11
    let len = d.array()?;
    cbor::check_tagged_array_length(0, len, 4)?;       // V16 sends 5 fields; unknown versions send anything
    ...
```

So V16 (`[magic, initiatorOnly, peerSharing, query, perasSupport]`) and any unknown version
make `decode_with` fail, the `?` aborts the table decode, the handshake child dies and the
connection is closed before negotiation.

The Haskell reference (`ouroboros-network`,
`framework/lib/Ouroboros/Network/Protocol/Handshake/Codec.hs`, `decodeVersions`) reads each
entry's parameters as an **opaque CBOR term** and **skips version numbers it does not
recognise**:

```haskell
vNumberTerm <- CBOR.decodeTerm
vParams <- CBOR.decodeTerm
case decodeTerm versionNumberCodec vNumberTerm of
  -- error when decoding un-recognized version; skip the version
  Left _        -> go (pred l) prev vs
  Right vNumber -> ...
```

Only the negotiated version's parameters are decoded afterwards (for V16,
`cardano-diffusion` `Cardano.Network.NodeToNode.Version` accepts the optional
`perasSupport` field), and a failure there is answered with `MsgRefuse HandshakeDecodeError`
rather than a dropped connection. That is why Cardano-node 11.1.2 answers the offer that
contains the unknown version 99 with `MsgAcceptVersion 15` (DWARF run
`20260923T150024Z-5ac32f61`).

## Severity

**Medium.**

- **Interoperability / availability:** any Cardano-node ≥ 11.1 with experimental protocols
  enabled (common on testnets and developer setups) cannot use an Amaru node as an upstream.
  An Amaru relay silently loses those inbound peers; an Amaru-fed Cardano node stalls.
- **Latent network-wide break:** the defect is not V16-specific. When Cardano-node ships any
  release that offers a new version with new parameters by default (V16/Peras is the next
  one), every such peer is refused by every Amaru responder at once.
- **Not affected today on public networks:** mainnet and preprod configs leave
  `ExperimentalProtocolsEnabled` unset (false), so current production Cardano-node initiators
  offer ≤ V15.
- **Not a crash / not consensus:** the node keeps running; no chain or ledger state is involved.
  A remote peer can only cause its own connection to be dropped.

## Scope

- Affected: Amaru as **responder** (inbound). Amaru as initiator offers only V11–V15, so its own
  outbound connections to Cardano-node 11.1.x succeed.
- Affected releases: every release with the current `VersionTable` decoder —
  `v10.11.20260918` (DWARF run), `v10.11.20260912` (DWARF qualification),
  `v10.11.20260903` (manual A/B), and `main` `5a2a08bc` (decoder source byte-identical to
  `v10.11.20260918`).
- The same code path is used for node-to-client handshakes wherever `VersionTable` is decoded
  (not tested here).

## Reproduction

DWARF (retained evidence, SARIF, measurements):

```bash
python3 dwarf/cardano-profile deploy profile-zb-mixed-1112-amaru-20260918-nanoseconds-v3 --approve
python3 dwarf/cardano-profile scenario run dwarf/scenarios/amaru-n2n-handshake-version-table-forward-compat-20260918-mixed-1112.yaml
python3 dwarf/cardano-profile deploy profile-v-cardano-measurement-nanoseconds-v2 --replace --approve
python3 dwarf/cardano-profile scenario run dwarf/scenarios/cardano-n2n-handshake-version-table-forward-compat-1112.yaml
```

Minimal (any Amaru N2N port):

```bash
# MsgProposeVersions {14,15,16(perasSupport)} in one initiator mux SDU (mode bit 0, protocol 0)
printf '\x00\x00\x00\x00\x00\x00\x00\x19\x82\x00\xa3\x0e\x84\x18\x2a\xf4\x01\xf4\x0f\x84\x18\x2a\xf4\x01\xf4\x10\x85\x18\x2a\xf4\x01\xf4\xf4' | nc -q2 <amaru-host> 3000 | xxd
# Amaru: no bytes, connection closed.  Cardano-node 11.1.2: MsgAcceptVersion 16 (8301 10 85 …), or 15 without V16 support.
```

## Suggested remediation

Decode the version table the way the reference does:

1. Decode each version number; for versions Amaru does not support, **skip** the parameter item
   (`d.skip()`) instead of decoding it.
2. Decode parameters only for supported versions, with the shape of that exact version
   (and accept the optional `perasSupport` field once V16 is supported).
3. If a supported version's parameters fail to decode, drop that version (or reply
   `MsgRefuse`) rather than aborting the whole handshake without a reply.

Regression tests: the three DWARF payloads above, plus an offer containing only unknown
versions (expected: `MsgRefuse VersionMismatch`).

Until fixed, DWARF pins `ExperimentalProtocolsEnabled=false` for Cardano-node ≥ 11.1 in
topologies that include Amaru (`cardano_experimental_protocols_policy`), which is the
public-network default.

## How this was found

DWARF's version qualification recorded Cardano-node 11.1.2 + Amaru 10.11.20260912 as
*incompatible* (the Amaru-fed consumer stayed at its seed slot) without a cause. Re-reading the
retained logs showed the consumer's connections to Amaru closing during the handshake
(`BearerClosed`, Amaru `connection.child_died child="Handshake"` within 1 ms). The Haskell
V16 encoder and Amaru's decoder were then compared, an A/B with a real 11.1.2 initiator isolated
`ExperimentalProtocolsEnabled` as the trigger, and the behaviour was captured as the DWARF
case set `version-table-forward-compat-v1` with the `handshake_cases_match_expected` assertion.

## Artifacts

Evidence bundle: `reports/amaru-n2n-handshake-unknown-version-data-evidence/`.
