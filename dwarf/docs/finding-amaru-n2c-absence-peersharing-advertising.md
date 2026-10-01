# Source audit: Amaru N2C feature-absence + peer-sharing wired-vs-advertised inconsistency

**Type:** source audit (coverage/robustness datapoint). **Severity:** informational / low.
**Context:** authorized conformance testing on the local devnet; source-audit, reported to PRAGMA.
**Audited source:** amaru `9eb5971f` (2026-09-27). **Served campaign binary:** `eaf8ac3f` (2026-09-25).
Both exhibit the structural behaviors below (they are long-standing architectural facts, not version-specific).

## 1. Node-to-Client (N2C) is entirely absent (feature-absence)

All four node-to-client mini-protocols are commented out — amaru serves NO N2C:

`crates/amaru-protocols/src/protocol/mod.rs:189-192`
```
// pub const PROTO_N2C_CHAIN_SYNC: ProtocolId<Initiator> = ProtocolId::<Initiator>(5, PhantomData);
// pub const PROTO_N2C_TX_SUB:     ProtocolId<Initiator> = ProtocolId::<Initiator>(6, PhantomData);
// pub const PROTO_N2C_STATE_QUERY:ProtocolId<Initiator> = ProtocolId::<Initiator>(7, PhantomData);
// pub const PROTO_N2C_TX_MON:     ProtocolId<Initiator> = ProtocolId::<Initiator>(9, PhantomData);
```

Consequence: the entire operator-facing local interface (local-chain-sync, local-tx-submission,
local-state-query, local-tx-monitor) does not exist on amaru. cardano-node serves all four. A
dual-client N2C differential is therefore **impossible** — the result is a documented FEATURE-ABSENCE,
the same category as the absent Genesis/GDD mode and the absent P2P peer-governor. This explains the
near-zero N2C coverage in the DWARF catalog: it is an absence, not a test gap. Tooling built against a
cardano-node local socket (cardano-cli query, local tx submission) has no amaru counterpart.

## 2. Peer-sharing: responder wired despite advertising PeerSharing::Disabled

Amaru advertises `PeerSharing::Disabled` in its N2N handshake version data:
`crates/amaru-protocols/src/protocol_messages/version_table.rs:39`
```
let data = VersionData::new(network_magic, false, PeerSharing::Disabled, true);
```
…yet it registers the peer-sharing RESPONDER on the connection:
`crates/amaru-protocols/src/connection.rs:512` `register_peer_sharing_responder(...)`,
`:639` `PROTO_N2N_PEER_SHARE.erase()`.

So the N2N peer-sharing mini-protocol (id 10) responder is wired even though the advertised version data
says peer-sharing is disabled. This is a wired-vs-advertised inconsistency: a peer that initiates
proto-10 after a handshake in which amaru declared Disabled may still reach a registered responder. A
live probe of the actual response (serve a share / refuse / empty) is a WIRE-lane follow-up (N2N
driving); the earlier peer-sharing content probe (G5) was a clean-negative, so this is specifically the
advertising-consistency angle, not a content divergence. Flagged for triage — whether it rises to a
reportable finding depends on the live responder behavior (potential topology-privacy surprise if it
actually shares peers after advertising Disabled).

## Reproduce via DWARF

This is a source-audit datapoint; the "test" is the absence/inconsistency itself:
- **N2C absence:** attempt any node-to-client connection to amaru → no N2C protocol is registered
  (mod.rs:189-192 commented) → no counterpart to cardano-node's local socket. Declarative source-audit
  scenario; no live differential is possible (that is the finding).
- **Peer-sharing (wire-lane live follow-up):** after an N2N handshake where amaru advertised
  `PeerSharing::Disabled`, initiate proto-10 and record the responder's reply vs cardano-node's. Needs
  the N2N driver (wire lane).

No GHSA: item 1 is a documented feature-absence (not an exploitable defect); item 2 is an informational
inconsistency pending live confirmation. Surfaced to the orchestrator for the reportability call.
