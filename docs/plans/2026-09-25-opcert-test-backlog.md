# Opcert Test Backlog — Untapped Dimensions

**Date:** 2026-09-25

## Context

DWARF already covers the six core operational-certificate (opcert) rules
deterministically — cold-key authentication, counter-behind, counter-jump,
counter-plus-one, kes-before-window, kes-after-window (plus valid-control) —
across the amaru-only, cardano-only, and mixed topologies. Beyond that
deterministic case-set, three randomized differential/soak families are now
built:

- **A `encoding-form`** — differential, done, 0 divergences.
- **D `kes-period-differential`** — differential, done, 0 divergences.
- **C `restart-persistence`** — counter-replay-after-restart, cardano running.
- **B `accept-boundary`** — excluded: non-observable, because a synthetic valid
  header's body is not adoptable, so the accept path never surfaces.

**Soak harness pieces referenced below:**

- Driver: `dwarf/scripts/runtime_opcert_header_soak.py`
- Generators: `dwarf/scripts/opcert_soak_families.py`
- Forger: `antithesis/components/dwarf-opcert-adversary-latest`
  (`serve-case --case-spec`)
- Parser: `dwarf/scripts/header_validation_parse.py`
- Assertions: `dwarf/profile_manager/primitives.py`

This document catalogs ten untapped opcert test dimensions. Each entry gives a
short description, the security relevance, whether it reuses the current harness
or needs new setup, which harness pieces it touches, and a priority
(P1 = high-value/cheap, P2, P3).

## Reuses current harness (cheap)

### 1. All-rules differential + reason parity — P1

Run the counter, cold-key, and KES-window families as cross-node differential
soaks (as A/D already do), but compare more than the accept/reject verdict:
compare the **rejection reason** as well. The invariant is semantic parity —
both nodes must reject, and reject for the *same* reason.

- **Security relevance:** Two implementations that reject the same header for
  different reasons hint at divergent validation logic that could be split by a
  crafted header. Reason parity catches near-misses that verdict parity hides.
- **Harness:** REUSES. Touches the driver differential path, parser reason
  extraction, a new reason-parity assertion, and the generators.

### 2. Cross-pool / multi-pool confusion — P1

Use pool A's cold-key signature or counter value on pool B's header, and
interleave multiple pools' opcerts in a single soak. Tests whether validation
tracks authorization *per pool* rather than globally.

- **Security relevance:** A node that authorizes headers with the wrong pool's
  cold key or counter would let one operator forge for another. Invariant:
  reject.
- **Harness:** REUSES. Touches the generators and the forger (serve a
  cross-pool cold-key / counter).

### 3. KES-evolution correctness — P1

Sign a header with a KES key that is under- or over-evolved for the claimed
period — the KES period is inside the valid window, but the key evolution does
not match it.

- **Security relevance:** Correct KES evolution is what binds a signature to a
  specific period; accepting a wrong-evolution signature would weaken forward
  security. Invariant: reject.
- **Harness:** REUSES. Touches the forger (KES evolve control) and the
  generators.

### 4. Error-precedence — P2

Break two rules in one header (e.g. counter-behind *and* kes-after-window) and
observe which error is reported — then check that both nodes agree on the
precedence.

- **Security relevance:** Disagreement on which error wins is another form of
  divergent validation ordering and can mask a bypass. Invariant: same reported
  error on both nodes.
- **Harness:** REUSES. Touches the generators and the parser.

### 5. Deeper counter edge cases — P2

Sweep counter overflow (max uint64), counter 0, large jumps, an exact
+1-accept / +2-reject boundary sweep, and replay after multiple legitimate
rotations.

- **Security relevance:** Off-by-one and overflow handling around the counter is
  a classic source of accept/reject mistakes; the boundary sweep pins the exact
  accept/reject edge.
- **Harness:** REUSES. Touches the generators.

### 6. Opcert-field CBOR mutations — P2 — DONE (decoder-level reframe)

Mutate individual opcert fields — negative or oversized counter, field type
confusion, missing / duplicate / extra opcert fields, truncated fixed-width
byte fields — going deeper than A's whole-header re-encoding.

- **Security relevance:** Malformed structural fields probe the decoder and the
  boundary between decode errors and validation errors; a lenient decoder can
  admit a header a strict one rejects.
- **Reframe (why NOT the live soak):** the differential soak correlates verdicts
  by `header_hash`; a malformed / value-changing opcert CBOR either fails to
  decode (no `header_hash` logged) or changes the hash, so the live soak scores
  these mutations INCONCLUSIVE, never "agreement" (confirmed by encoding-form's
  25/98 inconclusive). Built instead as a DECODER-LEVEL differential: feed the
  same opcert-field-mutated header CBOR to both header decoders and diff.
- **Harness:** `dwarf/scripts/opcert_field_decoder_diff.py` +
  `tests/test_opcert_field_decoder_diff.py`; forger `mutate-opcert` (corpus) and
  `decode-praos-header` (cardano decoder) modes; amaru
  `amaru-cbor-decode-block-header`.
- **Result:** FINDING — Amaru's header decoder is more lenient than
  cardano-node's at the opcert level: it accepts a truncated hot-vkey, a
  truncated cold-sig, and an extra opcert field that cardano-node rejects at
  decode (`dwarf/docs/finding-opcert-field-decode-leniency.md`). The other 8
  field mutations are rejected by both.

## Needs new setup, adjacent

### 7. Epoch-boundary semantics — P2 — THIN / DEFERRED (not built)

Exercise opcert counter and KES behavior as the chain crosses an epoch boundary.

- **Verdict (design review 2026-09-26): opcert validation is epoch-INVARIANT — no
  epoch-dependent opcert check to probe.** Counter monotonicity uses per-pool
  ledger state that persists monotonically across epochs (no epoch reset); the
  KES-period window is slot-based (kp = slot / slotsPerKESPeriod) and on the
  profiles slotsPerKESPeriod=129600 gives an ~18h KES period that dwarfs the
  ~200s epoch (epochLength 400 x 0.5s), so kp does not move across a boundary;
  cold/hot-key checks are epoch-independent. Reachability is fine (an epoch every
  ~200s) — the gap is SIGNAL, not reachability.
- **Where the epoch-dependent surface actually is:** VRF / leader-schedule (nonce
  roll) — already covered by the VRF false-leadership finding
  (`consensus-report-false-leadership-vrf.md`) — and pool lifecycle (see #8). A
  before/after-boundary opcert soak would only re-confirm the epoch-invariance of
  rules already proven at parity (#1/#4/#5), so it is deferred as low-value.
- **Harness:** NEW setup — the run must span an epoch (would be trivial; the
  value, not the reachability, is the blocker).

### 8. Retired / deregistered-pool forging — P3

Forge an opcert header from a pool that has retired or deregistered.

- **Security relevance:** A retired pool should no longer be able to produce
  adoptable headers; accepting one is an authorization-lifecycle bug.
- **Harness:** NEW setup — requires pool lifecycle (registration/retirement)
  provisioning.


- **Reachability / masking verdict (probe analysis 2026-09-26): the framed test —
  reject a retired/deregistered pool's header on "opcert-issuer / pool-not-registered"
  — is NOT cleanly observable via live header serving.** Header validation gates the
  opcert check behind VRF/leadership, and pool retirement is PIPELINED: (a) during
  the retirement epoch and until the stake snapshot ages out (~2 epochs), the pool is
  removed from registration but remains leader-eligible via the lagged stake snapshot
  and keeps forging VALID, accepted headers (normal wind-down) — no rejection to test;
  (b) once it ages out of the leadership snapshot its headers fail VRF/leadership first
  (both nodes reject and AGREE — VRF false-leadership finding), masking any opcert
  check. There is no state where a header is simultaneously valid-leader AND
  opcert-issuer-unknown, so the pool-identity check is unreachable at the header layer
  (the same masking class as the #6 by-hash limit).
- **Residual empirical caveat:** whether the protocol-state opcert-counter map is
  cleaned exactly at retirement could, in principle, produce a transient lag-window
  divergence — but that window is narrow and hard to hit deterministically, and a
  retired pool forging during the lag is normal accepted behaviour. Would require a
  validator-level (non-serving) delivery to probe, not a live soak. Deferred.
### 9. Genesis / OBFT-delegate opcerts & cross-era validation — P3

Cover genesis-delegate opcerts, which follow a different validation path, and
opcert validation across era boundaries (Conway vs Dijkstra).

- **Security relevance:** Alternate validation paths and cross-era rules are
  under-tested surfaces where divergence between implementations is plausible.
- **Harness:** NEW setup — genesis-delegate keys and a multi-era run.

## New sub-project (network level)

### 10. Mesh injection (phase 2) — P2

Inject a bad-opcert header into a live mesh and observe network-level effects:
does it propagate, split the chain, or get the peer disconnected? Cover
peer-punishment / DoS on repeated bad opcerts, and eclipse-with-bad-opcerts.

- **Security relevance:** Moves from "does the node reject it" to "what does the
  network do about a misbehaving peer" — propagation, chain safety, and
  DoS/eclipse resistance.
- **Harness:** NEW network-level harness (beyond the current single-header
  soak).

- **Probe verdict (2026-09-26) — angle 1 (crash-propagation) = CONTAINED, not amplified.**
  Framed as: does the confirmed Amaru non-canonical-CBOR crash (finding
  `finding-amaru-noncanonical-cbor-crash.md`) propagate across the mesh? Crux probe: does a
  relaying cardano-node forward the raw non-canonical bytes or canonicalize them? EMPIRICAL result
  (flipped my initial raw-forwarding lean): cardano-node computes the CANONICAL hash for a served
  non-canonical header (×4, ingest-hash = canonical id), i.e. it decodes to the typed header and
  discards the raw bytes → it forwards the canonical form → the crash does NOT propagate. Blast
  radius = Amaru nodes an attacker DIRECTLY peers with (targeted per-connection DoS); a cardano
  relay sanitizes, and an Amaru node crashes on receipt before it could re-serve. Written up as the
  "Blast radius / mesh containment" section of the crash finding. (Clean 2-hop forger→cardano→amaru
  confirmation blocked by one-shot-forger sync lag; containment rests on ingest-hash + cardano's
  typed-header architecture.)
- **Angles 2 (chain-split containment) and 4 (eclipse) = THIN:** the crash pre-empts any persistent
  divergent-tip split, and an eclipsed target just dies rather than being fed a divergent view.
  Angle 3 (peer-disconnect/ban) not separately pursued (a rider on the contained result).
- **Status: not built** (contained result documented; a full network-level harness is not warranted
  for a non-propagating crash).

## Status / next

Dimensions **#1–#3** are being implemented now as the P1 batch. The remaining
dimensions (#4–#10) are backlog.
