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
)

DIFFERENTIAL_FAMILIES: frozenset[str] = frozenset({"encoding-form", "kes-period-differential"})

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


def iter_rng(family: str, seed: int, iteration: int) -> random.Random:
    """A deterministic RNG unique to ``(family, seed, iteration)``."""
    return random.Random(f"{family}:{seed}:{iteration}")


def _case_id(family: str, iteration: int) -> str:
    return f"{family}-{iteration:06d}"


def generate_case(family: str, seed: int, iteration: int, *, restart_k: int = 4) -> dict:
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

    # kes-period-differential
    base.update(base_case="valid-control", expected_verdict="accept",
                expected_reason=None,
                params={"slot_offset_fraction": rng.random()})
    return base
