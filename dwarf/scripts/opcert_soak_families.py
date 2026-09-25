"""Seed-deterministic per-family case generators for the opcert soaks.

``generate_case(family, seed, iteration)`` returns a JSON-serialisable
``SoakCaseSpec`` that is fully determined by ``(family, seed, iteration)`` — so
any finding is replayable from the recorded spec. Randomization is *structured,
within a family only*: the generator never emits raw random header bytes (raw
byte fuzzing is out of scope, owned by the CBOR fuzzers). For the encoding-form
family the generator picks a structural deviation and a byte-seed; the forger
reproduces the exact bytes from that seed.

Each generator draws from ``iter_rng(family, seed, iteration)`` — a
``random.Random`` seeded on the string ``"{family}:{seed}:{iteration}"`` — so
two families / seeds / iterations never share a stream and replay is exact.
"""
from __future__ import annotations

import random

FAMILIES: tuple[str, ...] = (
    "encoding-form",
    "accept-boundary",
    "restart-persistence",
    "kes-period-differential",
    "cross-pool-confusion",
    "rules-differential",
    "kes-evolution",
)

DIFFERENTIAL_FAMILIES: frozenset[str] = frozenset({"encoding-form", "kes-period-differential", "rules-differential"})

ENCODING_FORMS: tuple[str, ...] = (
    "noncanonical-int",
    "definite-array",
    "indefinite-array",
    "extra-map-key",
    "duplicate-map-key",
    "missing-optional-key",
    "trailing-bytes",
)

# Per-node expected rejection reason for the restart-persistence family (a
# counter replayed below the rotated counter is CounterTooSmall on both nodes,
# each under its own token). Identical to the opcert case table's tokens.
_RESTART_REJECT_REASON = {
    "cardano-node": "CounterTooSmallOCERT",
    "amaru": "SequenceNumberTooSmall",
}


# Per-node expected rejection reason for the rules-differential family. Each key
# is a corpus reject-rule case id (== the forger `serve-case --case` id); the
# value is the per-implementation reason token the opcert case table declares
# (mirrors dwarf/corpora/opcert/opcert-header-cases-v1.json). ONLY the rules
# reachable on a plain mixed devnet are included: counter-behind (needs a prior
# opcert rotation so recorded>=1) and kes-after-window (needs the chain aged past
# maxKESEvolutions) are EXCLUDED -- the forger itself fails those closed on a
# fresh devnet -- so they are owned by the restart/aging families, not here.
_RULES_DIFFERENTIAL_REASON = {
    "cold-key-unauthorized": {"cardano-node": "InvalidSignatureOCERT",
                              "amaru": "InvalidSignature"},
    "counter-jump": {"cardano-node": "CounterOverIncrementedOCERT",
                     "amaru": "SequenceNumberTooFarAhead"},
    "kes-before-window": {"cardano-node": "KESBeforeStartOCERT",
                          "amaru": "OpCertKesPeriodTooLarge"},
    "hot-key-mismatch": {"cardano-node": "InvalidKesSignatureOCERT",
                         "amaru": "InvalidKesSignature"},
}
RULES_DIFFERENTIAL_RULES: tuple[str, ...] = tuple(_RULES_DIFFERENTIAL_REASON)

# Seed-deterministic magnitude sweeps for the two magnitude rules. Both span
# small and LARGE values so the soak hunts a magnitude-dependent cardano-vs-amaru
# divergence (e.g. a node with a different "too far ahead" / "period too large"
# threshold). All magnitudes still trigger the SAME rule (counter-jump: any
# jump >= 2 over-increments; kes-before-window: any period >= 1 ahead is before
# the window), so the expected reject reason per node is unchanged.
COUNTER_JUMP_MAGNITUDES: tuple[int, ...] = (2, 3, 5, 13, 50, 250)
KES_PERIODS_AHEAD_MAGNITUDES: tuple[int, ...] = (1, 2, 4, 9, 25, 100)


def iter_rng(family: str, seed: int, iteration: int) -> random.Random:
    """A deterministic RNG unique to ``(family, seed, iteration)``."""
    return random.Random(f"{family}:{seed}:{iteration}")


def _case_id(family: str, iteration: int) -> str:
    return f"{family}-{iteration:06d}"


def generate_case(family: str, seed: int, iteration: int, *, restart_k: int = 4,
                  kes_evolution_aged: bool = False) -> dict:
    """Return a seed+iteration deterministic ``SoakCaseSpec`` dict."""
    if family not in FAMILIES:
        raise ValueError(f"unknown family: {family!r}")
    rng = iter_rng(family, seed, iteration)
    base = {
        "family": family,
        "seed": seed,
        "iteration": iteration,
        "case_id": _case_id(family, iteration),
    }

    if family == "encoding-form":
        form = rng.choice(ENCODING_FORMS)
        params = {"encoding_form": form, "byte_seed": rng.randrange(2**31)}
        if form == "trailing-bytes":
            params["trailing_len"] = rng.randint(1, 8)
        base.update(base_case="valid-control", expected_verdict="accept",
                    expected_reason=None, params=params)
        return base

    if family == "accept-boundary":
        base.update(base_case="counter-plus-one", expected_verdict="accept",
                    expected_reason=None,
                    params={"counter_delta": 1, "kes_period_fraction": rng.random()})
        return base

    if family == "restart-persistence":
        rotate_to = rng.randint(1, restart_k)
        replay = rng.randrange(rotate_to)  # 0 .. rotate_to-1
        base.update(base_case="counter-behind", expected_verdict="reject",
                    expected_reason=dict(_RESTART_REJECT_REASON),
                    params={"rotate_to_counter": rotate_to, "replay_counter": replay})
        return base

    if family == "cross-pool-confusion":
        return _crosspool_confusion_case(base, rng)

    if family == "rules-differential":
        # Pick one reachable reject rule per iteration (the rule choice IS one
        # axis of randomization). The forger now CONSUMES the recorded boundary:
        # ``counter_jump`` and ``kes_periods_ahead`` are seed-deterministic
        # magnitude SWEEPS (small..large) for the two magnitude rules, and
        # ``byte_seed`` seeds the forger's wrong cold-/hot-key derivation for the
        # cold-key-unauthorized / hot-key-mismatch rules -- so the served header
        # varies its violation magnitude / wrong key per iteration, hunting a
        # magnitude-dependent cardano-vs-amaru divergence.
        rule = rng.choice(RULES_DIFFERENTIAL_RULES)
        params = {"rule": rule, "byte_seed": rng.randrange(2**31)}
        if rule == "counter-jump":
            params["counter_jump"] = rng.choice(COUNTER_JUMP_MAGNITUDES)
        elif rule == "kes-before-window":
            params["kes_periods_ahead"] = rng.choice(KES_PERIODS_AHEAD_MAGNITUDES)
        base.update(base_case=rule, expected_verdict="reject",
                    expected_reason=dict(_RULES_DIFFERENTIAL_REASON[rule]),
                    params=params)
        return base

    if family == "kes-evolution":
        return _kes_evolution_case(base, rng, aged=kes_evolution_aged)

    # kes-period-differential
    base.update(base_case="valid-control", expected_verdict="accept",
                expected_reason=None,
                params={"slot_offset_fraction": rng.random()})
    return base


# --------------------------------------------------------------------------- #
# Family #2: cross-pool confusion (single-target). Serve a real pool1 header
# whose operational certificate is authorized by the WRONG pool -- its opcert
# signature (``ocertSigma``) is made by a DIFFERENT real devnet pool's cold key
# (pool2/pool3), while the header issuer cold vkey (``hbVk``) stays pool1. A
# node that scopes opcert authorization PER POOL verifies ``ocertSigma`` against
# pool1's cold vkey and rejects it (the signature is by another pool's key), so
# the invariant is REJECTED; an ACCEPT means the node treated another pool's
# authorization as valid for pool1 -- cross-pool confusion, a real finding.
#
# Unlike ``cold-key-unauthorized`` (which signs with a synthetic throwaway key),
# the wrong key here is a legitimate, network-authorized OTHER pool's cold key,
# so the test isolates *pool-scoped* authorization rather than merely "unknown
# key". The reject reason is the same opcert-signature rule token on each node.
# The forger derives the foreign pool's ``cold.skey`` from the sibling of the
# ``--cold-skey`` it is already given plus this ``foreign_pool`` param, so no
# driver change is needed to route a second pool's key.
# --------------------------------------------------------------------------- #
CROSSPOOL_FOREIGN_POOLS: tuple[str, ...] = ("pool2", "pool3")

# Per-node expected rejection reason for the cross-pool family: the opcert
# signature does not verify against the header issuer (pool1) cold vkey. Same
# tokens the opcert case table uses for an issuer-signature failure.
_CROSSPOOL_REJECT_REASON = {
    "cardano-node": "InvalidSignatureOCERT",
    "amaru": "InvalidSignature",
}


def _crosspool_confusion_case(base: dict, rng: random.Random) -> dict:
    """Populate ``base`` for one cross-pool-confusion iteration.

    Seed+iteration deterministic: the only structured variation is which real
    foreign pool authorizes the opcert (drawn from ``CROSSPOOL_FOREIGN_POOLS``),
    so replay from ``(seed, iteration)`` reproduces the exact foreign pool. The
    expected verdict is a per-node ``reject`` keyed by the issuer-signature rule
    token; an observed ``accept`` is scored a mismatch (finding) by the soak
    result layer.
    """
    foreign = rng.choice(CROSSPOOL_FOREIGN_POOLS)
    base.update(base_case="cross-pool", expected_verdict="reject",
                expected_reason=dict(_CROSSPOOL_REJECT_REASON),
                params={"foreign_pool": foreign,
                        "variant": "foreign-cold-authorization"})
    return base


# --------------------------------------------------------------------------- #
# Family #3: KES-evolution (single-target). Serve a real pool1 header whose KES
# signature is produced with the key evolved to the WRONG number of steps for
# the header's KES period: sign at evolution ``correctEvol + delta`` (delta != 0)
# while the opcert period/counter/cold-signature stay valid (the period itself
# is inside the valid window). The node verifies the KES signature at the
# expected evolution ``t = kp - c0`` and it fails, because a KES signature is
# period-bound: InvalidKesSignatureOCERT (cardano) / InvalidKesSignature
# (amaru). The invariant is REJECTED; an ACCEPT means the node accepted a KES
# signature whose evolution count does not match the header's period -- a real
# finding.
#
# Reachability note (live-confirmed): a NEGATIVE delta (under-evolution) is only
# reachable when ``correctEvol`` is large enough. On a fresh/young devnet the
# live tip's ``correctEvol == 0``, so under-evolution can never be served -- the
# forger correctly fail-closes it to an ``unreachable`` (inconclusive) iteration
# (never a false finding), but such iterations are wasted and an all-negative
# draw would make a run vacuous (0 conclusive -> the fail-closed soak FAILS).
# Therefore the generator emits POSITIVE-ONLY deltas by default
# (``KESEVO_POSITIVE_DELTAS``) so a fresh-devnet run is always conclusive; full
# under+over coverage requires an AGED-KES devnet profile (same aging
# requirement as kes-after-window), selected via ``kes_evolution_aged=True``
# (then ``KESEVO_DELTAS``, both signs).
# --------------------------------------------------------------------------- #
# Both signs -- for an AGED-KES devnet where under-evolution is reachable.
KESEVO_DELTAS: tuple[int, ...] = (-2, -1, 1, 2)
# Over-evolution only -- always reachable, so a fresh/young-devnet run is never
# vacuous. The default.
KESEVO_POSITIVE_DELTAS: tuple[int, ...] = (1, 2)

# Per-node expected rejection reason: the KES signature does not verify at the
# header's expected evolution. Same tokens the opcert case table uses for a KES
# signature failure (hot-key-mismatch).
_KESEVO_REJECT_REASON = {
    "cardano-node": "InvalidKesSignatureOCERT",
    "amaru": "InvalidKesSignature",
}


def _kes_evolution_case(base: dict, rng: random.Random, *, aged: bool = False) -> dict:
    """Populate ``base`` for one kes-evolution iteration.

    Seed+iteration deterministic: the only structured variation is the nonzero
    evolution delta, so replay from ``(seed, iteration)`` reproduces the exact
    delta. The delta is never 0 -- a zero delta is the *correct* evolution, which
    is a valid header the node accepts, so the family must always mutate.

    ``aged`` selects the delta set: the default (fresh/young devnet) draws
    POSITIVE-ONLY deltas (``KESEVO_POSITIVE_DELTAS``, over-evolution, always
    reachable) so a run is never vacuous; ``aged=True`` (an aged-KES devnet where
    under-evolution is reachable) draws both signs (``KESEVO_DELTAS``). The
    expected verdict is a per-node ``reject`` keyed by the KES-signature rule
    token; an observed ``accept`` is scored a mismatch (finding) by the soak
    result layer.
    """
    delta = rng.choice(KESEVO_DELTAS if aged else KESEVO_POSITIVE_DELTAS)
    base.update(base_case="kes-evolution", expected_verdict="reject",
                expected_reason=dict(_KESEVO_REJECT_REASON),
                params={"kes_evolution_delta": delta})
    return base
