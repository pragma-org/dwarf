"""UPLC term builder over the FLAT encoder (Plutus Core specification, untyped plutus core 1.0.0)."""
from __future__ import annotations

import pytest

from scripts import uplc_terms as u
from scripts.runtime_uplc_deep_delay_stack_safety import encode_deep_delay_program


def test_program_with_lambdas_delay_and_error_matches_the_existing_deep_delay_encoder():
    term = u.lam(u.lam(u.delay(u.error())))
    assert u.encode_program(term) == encode_deep_delay_program(1)


def test_integer_constants_use_zigzag_naturals():
    # constant integer -1 -> zigzag 1; 64 -> zigzag 128 needs two 7-bit groups
    assert u.zigzag(-1) == 1 and u.zigzag(64) == 128 and u.zigzag(0) == 0 and u.zigzag(-65) == 129
    a = u.encode_program(u.const_integer(-1)); b = u.encode_program(u.const_integer(-2))
    assert a != b and a[:3] == bytes([1, 0, 0])


def test_bytestring_constants_are_chunked_after_a_filler():
    small = u.encode_program(u.const_bytes(b"\x01\x02"))
    big = u.encode_program(u.const_bytes(bytes(300)))
    assert len(big) > 300 + 3  # two chunks (255 + 45) plus lengths and terminator
    assert small.endswith(b"\x01")  # program filler


def test_builtins_have_their_specification_tags():
    assert u.BUILTINS["divideInteger"] == 3 and u.BUILTINS["sha2_256"] == 18 and u.BUILTINS["ifThenElse"] == 26
    assert u.BUILTINS["verifyEd25519Signature"] == 21 and u.BUILTINS["bls12_381_G1_uncompress"] == 59
    assert u.BUILTIN_FORCES["ifThenElse"] == 1 and u.BUILTIN_FORCES["fstPair"] == 2 and u.BUILTIN_FORCES["addInteger"] == 0


def test_call_applies_forces_and_arguments_in_order():
    term = u.call("ifThenElse", u.const_bool(True), u.const_unit(), u.const_unit())
    assert term == ("apply", ("apply", ("apply", ("force", ("builtin", 26)), ("const", "bool", True)), ("const", "unit", None)), ("const", "unit", None))


def test_policy_returns_unit_when_the_check_holds_and_errors_otherwise():
    v2 = u.minting_policy(u.const_bool(True), language="PlutusV2")
    v3 = u.minting_policy(u.const_bool(True), language="PlutusV3")
    assert v2[0] == "lam" and v2[1][0] == "lam"  # redeemer, context
    assert v3[0] == "lam" and v3[1][0] != "lam"  # context only
    with pytest.raises(ValueError):
        u.minting_policy(u.const_bool(True), language="PlutusV9")


def test_variables_are_positive_de_bruijn_indices():
    with pytest.raises(ValueError):
        u.var(0)
    assert u.encode_program(u.lam(u.var(1)))[3] >> 4 == 2  # first term tag LAMBDA
