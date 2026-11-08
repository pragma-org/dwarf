"""Plutus case definitions for the on-chain differential: known-answer vectors and structure."""
from __future__ import annotations

import hashlib

import pytest

from scripts import plutus_cases as pc
from scripts import uplc_terms as u


def test_known_answer_vectors_are_the_published_values():
    assert pc.SHA2_256_EMPTY == hashlib.sha256(b"").digest()
    assert pc.BLAKE2B_256_EMPTY == hashlib.blake2b(b"", digest_size=32).digest()
    assert len(pc.ED25519_RFC8032_PUBLIC_KEY) == 32 and len(pc.ED25519_RFC8032_SIGNATURE) == 64
    assert len(pc.BLS12_381_G1_GENERATOR) == 48 and pc.BLS12_381_G1_GENERATOR[0] & 0x80
    assert pc.BLS12_381_G1_IDENTITY == bytes([0xC0]) + bytes(47)


def test_ed25519_vector_verifies_with_an_independent_library():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    key = ed.Ed25519PublicKey.from_public_bytes(pc.ED25519_RFC8032_PUBLIC_KEY)
    key.verify(pc.ED25519_RFC8032_SIGNATURE, b"")  # raises if the vector were wrong


def test_integer_division_expectations_follow_haskell_semantics():
    # div/mod floor towards negative infinity; quot/rem truncate towards zero
    assert (-7 // 2, -7 % 2) == (-4, 1) and (7 // -2, 7 % -2) == (-4, -1)
    assert int(-7 / 2) == -3 and -7 - int(-7 / 2) * 2 == -1


def test_every_family_has_an_accept_and_a_reject_case_and_a_control():
    families = pc.families()
    assert set(families) == {"cek-integer-division", "cek-value-structure", "crypto-builtins", "bls12-381-builtins"}
    for name, family in families.items():
        expectations = {case.expected for case in family.cases}
        assert expectations == {"accept", "reject"}, name
        assert family.control.expected == "accept" and family.control.language == family.language
        ids = [case.case_id for case in family.cases]
        assert len(ids) == len(set(ids))


def test_cases_encode_to_programs_with_stable_hashes():
    for family in pc.families().values():
        for case in (family.control, *family.cases):
            program = case.program()
            assert program[:3] == bytes([1, 0, 0])
            assert case.program_sha256() == hashlib.sha256(program).hexdigest()


def test_bls_family_uses_plutus_v3_and_the_rest_v2():
    families = pc.families()
    assert families["bls12-381-builtins"].language == "PlutusV3"
    assert {f.language for n, f in families.items() if n != "bls12-381-builtins"} == {"PlutusV2"}


def test_families_can_be_built_for_every_allowed_language():
    for name, languages in pc.FAMILY_LANGUAGES.items():
        for language in languages:
            family = pc.family(name, language)
            assert family.language == language and family.control.language == language
            assert all(case.language == language for case in family.cases)
    assert pc.FAMILY_LANGUAGES["bls12-381-builtins"] == ("PlutusV3",)
    with pytest.raises(ValueError):
        pc.family("bls12-381-builtins", "PlutusV2")


def test_language_selection_follows_the_live_cost_models():
    # this chain (testnet_42, protocol 10.0) carries PlutusV1 and PlutusV3 cost models only
    live = {"PlutusV1": [1], "PlutusV3": [1]}
    assert pc.select_language("crypto-builtins", live) == "PlutusV3"
    assert pc.select_language("crypto-builtins", {"PlutusV1": [1], "PlutusV2": [1]}) == "PlutusV2"
    assert pc.select_language("crypto-builtins", {"PlutusV1": [1]}) == "PlutusV1"
    assert pc.select_language("bls12-381-builtins", {"PlutusV1": [1], "PlutusV2": [1]}) is None
