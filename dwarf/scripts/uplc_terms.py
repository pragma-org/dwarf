"""Untyped Plutus Core terms and their FLAT encoding (Plutus Core specification, UPLC 1.0.0).

Terms are plain tuples so a case definition stays readable and hashable:

    ("var", index)            de Bruijn index, >= 1
    ("lam", body)             lambda; de Bruijn names carry no bits
    ("apply", fn, arg)
    ("delay", term) / ("force", term)
    ("error",)
    ("builtin", tag)
    ("const", type, value)    type in integer | bytestring | string | unit | bool | data

``encode_program`` writes the version (1.0.0), the term and the closing filler, the exact byte
form a script carries on chain. Nothing here depends on a node release: builtin tags and the
encoding are fixed by the Plutus Core specification.
"""
from __future__ import annotations

from typing import Any

import cbor2

try:  # run from the scripts directory (helpers) or as the scripts package (tests, primitives)
    from runtime_uplc_deep_delay_stack_safety import _FlatEncoder
except ImportError:  # pragma: no cover - import path depends on the caller
    from scripts.runtime_uplc_deep_delay_stack_safety import _FlatEncoder

# Builtin tags (Plutus Core specification, "Built-in functions").
BUILTINS: dict[str, int] = {
    "addInteger": 0, "subtractInteger": 1, "multiplyInteger": 2, "divideInteger": 3,
    "quotientInteger": 4, "remainderInteger": 5, "modInteger": 6, "equalsInteger": 7,
    "lessThanInteger": 8, "lessThanEqualsInteger": 9, "appendByteString": 10,
    "consByteString": 11, "sliceByteString": 12, "lengthOfByteString": 13,
    "indexByteString": 14, "equalsByteString": 15, "lessThanByteString": 16,
    "lessThanEqualsByteString": 17, "sha2_256": 18, "sha3_256": 19, "blake2b_256": 20,
    "verifyEd25519Signature": 21, "appendString": 22, "equalsString": 23, "encodeUtf8": 24,
    "decodeUtf8": 25, "ifThenElse": 26, "chooseUnit": 27, "trace": 28, "fstPair": 29,
    "sndPair": 30, "chooseList": 31, "mkCons": 32, "headList": 33, "tailList": 34,
    "nullList": 35, "chooseData": 36, "constrData": 37, "mapData": 38, "listData": 39,
    "iData": 40, "bData": 41, "unConstrData": 42, "unMapData": 43, "unListData": 44,
    "unIData": 45, "unBData": 46, "equalsData": 47, "mkPairData": 48, "mkNilData": 49,
    "mkNilPairData": 50, "serialiseData": 51, "verifyEcdsaSecp256k1Signature": 52,
    "verifySchnorrSecp256k1Signature": 53, "bls12_381_G1_add": 54, "bls12_381_G1_neg": 55,
    "bls12_381_G1_scalarMul": 56, "bls12_381_G1_equal": 57, "bls12_381_G1_compress": 58,
    "bls12_381_G1_uncompress": 59, "bls12_381_G1_hashToGroup": 60, "bls12_381_G2_add": 61,
    "bls12_381_G2_neg": 62, "bls12_381_G2_scalarMul": 63, "bls12_381_G2_equal": 64,
    "bls12_381_G2_compress": 65, "bls12_381_G2_uncompress": 66,
    "bls12_381_G2_hashToGroup": 67, "bls12_381_millerLoop": 68, "bls12_381_mulMlResult": 69,
    "bls12_381_finalVerify": 70, "keccak_256": 71, "blake2b_224": 72,
}

# Type-polymorphic builtins take one Force per type argument.
BUILTIN_FORCES: dict[str, int] = {name: 0 for name in BUILTINS}
BUILTIN_FORCES.update({
    "ifThenElse": 1, "chooseUnit": 1, "trace": 1, "fstPair": 2, "sndPair": 2,
    "chooseList": 2, "mkCons": 1, "headList": 1, "tailList": 1, "nullList": 1, "chooseData": 1,
})

_TERM_TAGS = {"var": 0, "delay": 1, "lam": 2, "apply": 3, "const": 4, "force": 5, "error": 6, "builtin": 7}
_TYPE_TAGS = {"integer": 0, "bytestring": 1, "string": 2, "unit": 3, "bool": 4, "data": 8}

# Plutus core versions per ledger language. 1.0.0 is valid for every language.
PROGRAM_VERSION = (1, 0, 0)
LANGUAGES = ("PlutusV1", "PlutusV2", "PlutusV3")


# --- constructors ---------------------------------------------------------------------------

def var(index: int):
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError("de Bruijn indices start at 1")
    return ("var", index)


def lam(body):
    return ("lam", body)


def apply(fn, *args):
    term = fn
    for arg in args:
        term = ("apply", term, arg)
    return term


def delay(term):
    return ("delay", term)


def force(term):
    return ("force", term)


def error():
    return ("error",)


def builtin(name: str):
    return ("builtin", BUILTINS[name])


def call(name: str, *args):
    """Apply a builtin with the forces its type arguments require."""
    fn = builtin(name)
    for _ in range(BUILTIN_FORCES[name]):
        fn = force(fn)
    return apply(fn, *args)


def const_integer(value: int):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("integer constant must be an int")
    return ("const", "integer", value)


def const_bytes(value: bytes):
    return ("const", "bytestring", bytes(value))


def const_string(value: str):
    return ("const", "string", str(value))


def const_bool(value: bool):
    return ("const", "bool", bool(value))


def const_unit():
    return ("const", "unit", None)


def const_data(value: Any):
    """A Data constant from a Python value: int, bytes, list, dict, or ("constr", i, [fields])."""
    return ("const", "data", value)


# --- combinators ----------------------------------------------------------------------------

def if_then_else(cond, then_term, else_term):
    """Strict-safe conditional: both branches are delayed and the chosen one forced."""
    return force(call("ifThenElse", cond, delay(then_term), delay(else_term)))


def all_true(*conditions):
    """Boolean conjunction of already-evaluated boolean terms."""
    if not conditions:
        return const_bool(True)
    term = conditions[-1]
    for cond in reversed(conditions[:-1]):
        term = call("ifThenElse", cond, term, const_bool(False))
    return term


def minting_policy(check, *, language: str):
    """A minting policy that returns unit when ``check`` is True and fails otherwise.

    V1/V2 minting policies take (redeemer, context); V3 scripts take the context only.
    """
    if language not in LANGUAGES:
        raise ValueError(f"unsupported Plutus language {language!r}")
    body = if_then_else(check, const_unit(), error())
    return lam(body) if language == "PlutusV3" else lam(lam(body))


# --- encoding -------------------------------------------------------------------------------

def zigzag(value: int) -> int:
    return value * 2 if value >= 0 else -value * 2 - 1


def data_to_cbor(value: Any) -> bytes:
    def encode(item):
        if isinstance(item, bool):
            raise ValueError("Data has no booleans")
        if isinstance(item, int):
            return item
        if isinstance(item, (bytes, bytearray)):
            if len(item) > 64:
                raise ValueError("Data bytestrings over 64 bytes need chunked encoding")
            return bytes(item)
        if isinstance(item, list):
            return [encode(x) for x in item]
        if isinstance(item, dict):
            return {encode(k): encode(v) for k, v in item.items()}
        if isinstance(item, tuple) and len(item) == 3 and item[0] == "constr":
            index, fields = int(item[1]), [encode(x) for x in item[2]]
            if 0 <= index <= 6:
                return cbor2.CBORTag(121 + index, fields)
            if 7 <= index <= 127:
                return cbor2.CBORTag(1280 + index - 7, fields)
            return cbor2.CBORTag(102, [index, fields])
        raise ValueError(f"unsupported Data value {item!r}")
    return cbor2.dumps(encode(value))


def _encode_bytes(encoder: _FlatEncoder, value: bytes) -> None:
    encoder.filler()
    for start in range(0, len(value), 255):
        chunk = value[start:start + 255]
        encoder.bits(8, len(chunk))
        for byte in chunk:
            encoder.bits(8, byte)
    encoder.bits(8, 0)


def _encode_constant(encoder: _FlatEncoder, kind: str, value: Any) -> None:
    encoder.bits(1, 1)
    encoder.bits(4, _TYPE_TAGS[kind])
    encoder.bits(1, 0)
    if kind == "integer":
        encoder.word(zigzag(value))
    elif kind == "bytestring":
        _encode_bytes(encoder, value)
    elif kind == "string":
        _encode_bytes(encoder, value.encode("utf-8"))
    elif kind == "bool":
        encoder.bits(1, 1 if value else 0)
    elif kind == "unit":
        pass
    elif kind == "data":
        _encode_bytes(encoder, data_to_cbor(value))
    else:
        raise ValueError(f"unsupported constant type {kind!r}")


def _encode_term(encoder: _FlatEncoder, term) -> None:
    kind = term[0]
    encoder.bits(4, _TERM_TAGS[kind])
    if kind == "var":
        encoder.word(term[1])
    elif kind in ("lam", "delay", "force"):
        _encode_term(encoder, term[1])
    elif kind == "apply":
        _encode_term(encoder, term[1])
        _encode_term(encoder, term[2])
    elif kind == "const":
        _encode_constant(encoder, term[1], term[2])
    elif kind == "builtin":
        encoder.bits(7, term[1])
    elif kind == "error":
        pass
    else:
        raise ValueError(f"unknown term {kind!r}")


def encode_program(term, *, version: tuple[int, int, int] = PROGRAM_VERSION) -> bytes:
    encoder = _FlatEncoder()
    for part in version:
        encoder.word(part)
    _encode_term(encoder, term)
    encoder.filler()
    return bytes(encoder.buffer)
