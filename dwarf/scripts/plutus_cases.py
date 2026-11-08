"""Plutus phase-2 cases for the on-chain Amaru / cardano-node differential.

Every case is a minting policy that evaluates one check and returns unit, or fails. Its expected
outcome comes from the Plutus Core specification, not from a recorded run: ``accept`` means the
policy succeeds and the transaction is admitted; ``reject`` means evaluation fails (a builtin
error or a false check) and a node must refuse the transaction. Each family carries a control
that always succeeds, so a rejection is never mistaken for coverage.

Nothing names a node release. The semantics are fixed by the Plutus language version, and the
transaction is built against the live chain's cost models and limits.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

try:  # scripts directory (helpers) or the scripts package (tests, primitives)
    import uplc_terms as u
except ImportError:  # pragma: no cover - import path depends on the caller
    from scripts import uplc_terms as u

# --- known-answer vectors ---------------------------------------------------------------------

SHA2_256_EMPTY = bytes.fromhex("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
BLAKE2B_256_EMPTY = bytes.fromhex("0e5751c026e543b2e8ab2eb06099daa1d1e5df47778f7787faab45cdf12fe3a8")
# RFC 8032 section 7.1, TEST 1 (empty message)
ED25519_RFC8032_PUBLIC_KEY = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
ED25519_RFC8032_SIGNATURE = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)
# BLS12-381 G1 generator and point at infinity, compressed (ZCash serialisation, CIP-0381)
BLS12_381_G1_GENERATOR = bytes.fromhex(
    "97f1d3a73197d7942695638c4fa9ac0fc3688c4f9774b905a14e3a3f171bac586c55e83ff97a1aeffb3af00adb22c6bb"
)
BLS12_381_G1_IDENTITY = bytes([0xC0]) + bytes(47)


@dataclass(frozen=True)
class Case:
    case_id: str
    language: str
    expected: str  # "accept" | "reject"
    check: Any = field(repr=False)
    description: str = ""

    def term(self):
        return u.minting_policy(self.check, language=self.language)

    def program(self) -> bytes:
        return u.encode_program(self.term())

    def program_sha256(self) -> str:
        return hashlib.sha256(self.program()).hexdigest()


@dataclass(frozen=True)
class Family:
    name: str
    language: str
    control: Case
    cases: tuple[Case, ...]


def _i(value: int):
    return u.const_integer(value)


def _b(value: bytes):
    return u.const_bytes(value)


def _eq_int(term, value: int):
    return u.call("equalsInteger", term, _i(value))


def _eq_bytes(term, value: bytes):
    return u.call("equalsByteString", term, _b(value))


def _not(term):
    return u.call("ifThenElse", term, u.const_bool(False), u.const_bool(True))


def _control(language: str) -> Case:
    return Case(f"control-{language.lower()}", language, "accept", u.const_bool(True),
                "policy that always succeeds; proves the transaction path")


def _integer_division(lang: str = "PlutusV2") -> Family:
    division = u.all_true(
        _eq_int(u.call("divideInteger", _i(-7), _i(2)), -4),
        _eq_int(u.call("modInteger", _i(-7), _i(2)), 1),
        _eq_int(u.call("quotientInteger", _i(-7), _i(2)), -3),
        _eq_int(u.call("remainderInteger", _i(-7), _i(2)), -1),
        _eq_int(u.call("divideInteger", _i(7), _i(-2)), -4),
        _eq_int(u.call("modInteger", _i(7), _i(-2)), -1),
        _eq_int(u.call("quotientInteger", _i(7), _i(-2)), -3),
        _eq_int(u.call("remainderInteger", _i(7), _i(-2)), 1),
    )
    return Family("cek-integer-division", lang, _control(lang), (
        Case("integer-division-signs", lang, "accept", division,
             "div/mod floor and quot/rem truncate on negative operands"),
        Case("integer-division-by-zero", lang, "reject", _eq_int(u.call("divideInteger", _i(1), _i(0)), 0),
             "divideInteger by zero is a builtin error"),
        Case("integer-division-wrong-expectation", lang, "reject",
             _eq_int(u.call("divideInteger", _i(-7), _i(2)), -3),
             "a false check must fail: proves the check discriminates"),
    ))


def _value_structure(lang: str = "PlutusV2") -> Family:
    big = 2 ** 100
    nested = ("constr", 0, [1, b"\xff", [2, 3]])
    built = u.call("constrData", _i(0), u.call("mkCons", u.call("iData", _i(1)), u.call("mkCons", u.call("bData", _b(b"\xff")),
        u.call("mkCons", u.call("listData", u.call("mkCons", u.call("iData", _i(2)), u.call("mkCons", u.call("iData", _i(3)),
            u.call("mkNilData", u.const_unit())))), u.call("mkNilData", u.const_unit())))))
    return Family("cek-value-structure", lang, _control(lang), (
        Case("bignum-arithmetic", lang, "accept", u.all_true(
            _eq_int(u.call("multiplyInteger", _i(big), _i(big)), big * big),
            _eq_int(u.call("subtractInteger", _i(big * big), _i(1)), big * big - 1),
            u.call("lessThanInteger", _i(2 ** 128), _i(2 ** 129)),
        ), "integers far beyond 64 bits"),
        Case("bytestring-edges", lang, "accept", u.all_true(
            _eq_int(u.call("lengthOfByteString", u.call("appendByteString", _b(b"\x00\xff"), _b(b""))), 2),
            _eq_int(u.call("indexByteString", _b(b"\x00\xff"), _i(1)), 255),
            _eq_bytes(u.call("sliceByteString", _i(1), _i(1), _b(b"\x00\xff\x11")), b"\xff"),
        ), "length, index and slice at the bounds"),
        Case("bytestring-index-out-of-range", lang, "reject",
             _eq_int(u.call("indexByteString", _b(b"\x00\xff"), _i(2)), 0),
             "indexByteString past the end is a builtin error"),
        Case("data-structural-equality", lang, "accept", u.call("equalsData", u.const_data(nested), built),
             "a Data constant equals the same value built with constructors"),
    ))


def _crypto(lang: str = "PlutusV2") -> Family:
    verify = lambda sig: u.call("verifyEd25519Signature", _b(ED25519_RFC8032_PUBLIC_KEY), _b(b""), _b(sig))  # noqa: E731
    tampered = bytes([ED25519_RFC8032_SIGNATURE[0] ^ 1]) + ED25519_RFC8032_SIGNATURE[1:]
    return Family("crypto-builtins", lang, _control(lang), (
        Case("sha2-256-known-answer", lang, "accept", _eq_bytes(u.call("sha2_256", _b(b"")), SHA2_256_EMPTY)),
        Case("blake2b-256-known-answer", lang, "accept", _eq_bytes(u.call("blake2b_256", _b(b"")), BLAKE2B_256_EMPTY)),
        Case("ed25519-rfc8032-vector", lang, "accept", verify(ED25519_RFC8032_SIGNATURE)),
        Case("ed25519-tampered-signature-is-false", lang, "accept", _not(verify(tampered)),
             "a wrong signature returns False, it does not fail"),
        Case("ed25519-malformed-signature", lang, "reject", verify(ED25519_RFC8032_SIGNATURE[:63]),
             "a 63-byte signature is a builtin error"),
    ))


def _bls12_381(lang: str = "PlutusV3") -> Family:
    point = u.call("bls12_381_G1_uncompress", _b(BLS12_381_G1_GENERATOR))
    return Family("bls12-381-builtins", lang, _control(lang), (
        Case("g1-compress-roundtrip", lang, "accept",
             _eq_bytes(u.call("bls12_381_G1_compress", point), BLS12_381_G1_GENERATOR)),
        Case("g1-add-negation-is-identity", lang, "accept", _eq_bytes(
            u.call("bls12_381_G1_compress", u.call("bls12_381_G1_add", point, u.call("bls12_381_G1_neg", point))),
            BLS12_381_G1_IDENTITY)),
        Case("g1-uncompress-invalid-point", lang, "reject", _eq_bytes(
            u.call("bls12_381_G1_compress", u.call("bls12_381_G1_uncompress", _b(bytes(48)))), bytes(48)),
            "48 zero bytes lack the compression flag: a builtin error"),
    ))


_BUILDERS = {
    "cek-integer-division": _integer_division,
    "cek-value-structure": _value_structure,
    "crypto-builtins": _crypto,
    "bls12-381-builtins": _bls12_381,
}

# Languages each family may run in, most current first. The checks use only builtins every listed
# language has, so the expected outcomes are the same in each; BLS12-381 builtins exist in V3 only.
FAMILY_LANGUAGES: dict[str, tuple[str, ...]] = {
    "cek-integer-division": ("PlutusV3", "PlutusV2", "PlutusV1"),
    "cek-value-structure": ("PlutusV3", "PlutusV2", "PlutusV1"),
    "crypto-builtins": ("PlutusV3", "PlutusV2", "PlutusV1"),
    "bls12-381-builtins": ("PlutusV3",),
}


def families() -> dict[str, Family]:
    return {name: builder() for name, builder in _BUILDERS.items()}


def family(name: str, language: str) -> Family:
    if language not in FAMILY_LANGUAGES[name]:
        raise ValueError(f"family {name} cannot run as {language}")
    return _BUILDERS[name](language)


def select_language(name: str, cost_models: dict) -> str | None:
    """The first allowed language the live chain has a cost model for, or None."""
    for language in FAMILY_LANGUAGES[name]:
        if isinstance((cost_models or {}).get(language), list):
            return language
    return None
