# Finding (CONFIRMED, advisory-first): amaru trusts a substituted `--peer-snapshot` wholesale — adversary big-ledger-peers dialed with no on-chain re-validation (eclipse-assist)

**Status:** CONFIRMED live on the unmodified binary. advisory-first — HOLD public until the user files.
**Severity:** MEDIUM (eclipse-assist / peer-trust boundary). Not remote-unauthenticated: requires control of the peer-snapshot provisioning (operator misconfig, supply-chain of the snapshot file, or MITM of its source). No on-chain self-correction once substituted.
**Target:** amaru v10.11.0 (`eaf8ac3f`, git_dirty=false) vs cardano-node 11.1.2. Conway, testnet_42, local devnet, testnet-only keys, coordinated disclosure to PRAGMA.

## Summary

amaru accepts a `--peer-snapshot` (`bigLedgerPools`) JSON file and uses its relays as big-ledger-peers **validating only the file's NetworkMagic** — it does **not** re-validate the relays against on-chain stake, and amaru has **no independent top-90%-stake big-ledger-peer computation** of its own. A substituted snapshot whose `bigLedgerPools` point at adversary relays therefore makes amaru **add and dial the adversary peers as big-ledger-peers**, with no on-chain correction. cardano-node derives its big-ledger-peer / ledger-peer set from on-chain ledger stake (self-correcting); amaru relies entirely on the external file, so the snapshot is a single spoofable trust root for peer selection = an eclipse-assist vector.

## Source anchor

- `crates/amaru-node/src/peer_snapshot.rs` (`load_peer_snapshot` / `parse_peer_snapshot_bytes`): validates the file's `NetworkMagic` against the expected magic, then loads `bigLedgerPools[].relays[]` (address/port) wholesale into the snapshot peer source. No stake field is checked; `accumulatedStake`/`relativeStake` are not verified against the node's own ledger.
- CLI `--peer-snapshot <FILEPATH>` (`amaru/src/bin/amaru/cmd/node/run.rs`): "Path to a Cardano ledger peer snapshot JSON file (`bigLedgerPools`)… When omitted, Amaru uses the snapshot embedded at build time."
- Peer governor `amaru-consensus/src/stages/peer_selection/`: the `snapshot` source feeds candidates into the peer mix (`PeerSource::Snapshot`); amaru's `ledger` source (`amaru-ledger/src/registered_relay_addrs.rs`) is an **unweighted** relay set (no stake weighting / no top-90% selection), so there is no on-chain big-ledger-peer computation to cross-check the snapshot against.

## Live evidence (unmodified `eaf8ac3f`, disposable amaru on :3399)

Crafted a substituted snapshot — `NetworkMagic:42`, `bigLedgerPools:[{relays:[192.0.2.66:3001, 192.0.2.67:3001]}]}` (TEST-NET-1 unroutable adversary markers) — and launched `amaru node run … --peer-snapshot <substituted>`:
- `peer_snapshot.loaded … path:<substituted-peer-snapshot.json>` — loaded with no content validation error.
- `peer_selection.connect_initial snapshot_peers:2 static_peers:1` — both substituted relays taken from the snapshot source.
- `peer_selection.peer.added peer:"192.0.2.66:3001"` and `peer:"192.0.2.67:3001"` — adversary relays added to the peer set.
- `manager.peer.connect peer:"192.0.2.66:3001"` / `"192.0.2.67:3001"` (repeated; `connect_failed` only because the marker addresses are unroutable) — amaru **dialed** the adversary peers.
- No stake re-validation / snapshot rejection anywhere in the log (the only ERRORs were unrelated stale-header rejections from the localhost static peer).

So amaru added **and** attempted to connect to adversary big-ledger-peers sourced entirely from the substituted file, with zero on-chain re-validation.

## Impact

An actor who can substitute or MITM the `--peer-snapshot` file (operator misconfiguration, compromised snapshot distribution/supply-chain, or an unauthenticated fetch of the snapshot source) can steer an amaru node's big-ledger-peer set to adversary-controlled relays. Because amaru has no on-chain big-ledger-peer computation, there is **no self-correction** — unlike cardano-node, which recomputes its ledger/big-ledger peers from on-chain stake. This is an eclipse-assist / peer-selection trust-boundary weakness, not a remote-unauthenticated compromise.

## Reproduce via DWARF

Primitive `substitute_big_ledger_peers` (now REAL) in `dwarf/scripts/runtime_topology_fault.py` → `_run_snapshot_substitution(config)`: crafts a substituted `bigLedgerPools` snapshot with adversary relays, launches a disposable amaru with `--peer-snapshot`, and measures `snapshot_loaded` / `peers_added_from_snapshot` / `dialed_substituted_peers` / `revalidated_against_chain`, verdict `TRUST_BOUNDARY_VIOLATION` iff amaru added+dialed the adversary relays without re-validation. Config: `{amaru_binary, amaru_store (pristine ledger+chain+era-history to copy), network, network_magic, globals_env, adversary_relays, observe_seconds}`.

## Remediation

amaru should not treat `--peer-snapshot` relays as trusted big-ledger-peers without corroboration: compute/cross-check the big-ledger-peer set from its own on-chain ledger stake (as cardano-node does), or treat the snapshot strictly as an untrusted bootstrap hint subject to on-chain correction once synced. At minimum, authenticate the snapshot source and document the trust assumption.
