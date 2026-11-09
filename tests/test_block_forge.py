"""Block forging: body commitments and header re-signing checked against real blocks."""
from __future__ import annotations

import json
from pathlib import Path

import cbor2
import pytest

from scripts import block_forge, praos_kes

FIX = Path(__file__).resolve().parent / "fixtures" / "block_apply"


def _network_block(name):
    era, raw_block = block_forge.array_items((FIX / name).read_bytes())
    return cbor2.loads(era), raw_block


@pytest.mark.parametrize("name", ["preprod-block-b9bef52d.cbor", "block-b99a6117-6tx.cbor"])
def test_body_hash_and_size_match_the_real_header(name):
    _era, raw_block = _network_block(name)
    header, segments = block_forge.split_block(raw_block)
    fields = block_forge.header_fields(header)
    assert block_forge.body_hash(segments).hex() == fields["body_hash"]
    assert block_forge.body_size(segments) == fields["body_size"]


@pytest.mark.parametrize("name", ["preprod-block-b9bef52d.cbor", "block-b99a6117-6tx.cbor"])
def test_transactions_rebuild_byte_identical_segments(name):
    _era, raw_block = _network_block(name)
    _header, (bodies, wits, aux, invalid) = block_forge.split_block(raw_block)
    aux_map = cbor2.loads(aux)
    aux_raw = {}
    # recover each aux entry's exact bytes from the map
    cursor = block_forge._head(aux, 0)[2]
    for _ in range(len(aux_map)):
        key_end = block_forge.item_end(aux, cursor)
        value_end = block_forge.item_end(aux, key_end)
        aux_raw[cbor2.loads(aux[cursor:key_end])] = aux[key_end:value_end]
        cursor = value_end
    invalid_set = set(cbor2.loads(invalid))
    txs = [
        block_forge.TxParts(b, w, i not in invalid_set, aux_raw.get(i))
        for i, (b, w) in enumerate(zip(block_forge.array_items(bodies), block_forge.array_items(wits)))
    ]
    assert block_forge.body_segments(txs) == (bodies, wits, aux, invalid)


def test_split_transaction_and_is_valid_false_lands_in_invalid_indices():
    tx = block_forge.encode_array([cbor2.dumps({0: []}), cbor2.dumps({}), b"\xf4", b"\xf6"])
    parts = block_forge.split_transaction(tx)
    assert parts.is_valid is False and parts.auxiliary is None
    segments = block_forge.body_segments([parts])
    assert segments == (b"\x81" + cbor2.dumps({0: []}), b"\x81\xa0", b"\xa0", b"\x81\x00")


def test_empty_body_segments_are_the_canonical_empty_body():
    assert block_forge.body_segments([]) == block_forge.EMPTY_BODY_SEGMENTS
    assert block_forge.body_size(block_forge.EMPTY_BODY_SEGMENTS) == 4


def _vector(index=0):
    case = json.loads((FIX / "haskell-header-vectors.json").read_text())["cases"][index]
    return bytes.fromhex(case["header"]), bytes.fromhex(case["kesSignKey"]), case["praosSlotsPerKESPeriod"]


def test_resign_without_changes_reproduces_the_haskell_header():
    header, sk, spk = _vector()
    assert block_forge.resign_header(header, kes_sign_key=sk, slots_per_kes_period=spk) == header


def test_resign_with_new_body_and_parent_is_a_valid_kes_signature():
    header, sk, spk = _vector(1)
    segments = block_forge.body_segments([])
    parent = bytes(range(32))
    forged = block_forge.resign_header(header, kes_sign_key=sk, slots_per_kes_period=spk,
                                       prev_hash=parent, segments=segments)
    body, sig = block_forge.decode_header(forged)
    original, _ = block_forge.decode_header(header)
    assert body[2] == parent
    assert body[7] == block_forge.body_hash(segments) and body[6] == 4
    # slot, VRF, issuer and opcert are untouched
    assert [body[i] for i in (0, 1, 3, 4, 5, 8, 9)] == [original[i] for i in (0, 1, 3, 4, 5, 8, 9)]
    period = block_forge.kes_evolution(body, spk)
    assert praos_kes.verify(bytes(body[8][0]), period, cbor2.dumps(body), sig)
    assert block_forge.header_hash(forged) != block_forge.header_hash(header)


def test_resign_refuses_a_key_the_opcert_does_not_certify():
    header, _sk, spk = _vector()
    other = praos_kes.load_sign_key(FIX / "kes.skey")
    with pytest.raises(ValueError, match="opcert hot key"):
        block_forge.resign_header(header, kes_sign_key=other, slots_per_kes_period=spk)


def test_item_end_reports_incomplete_buffers():
    raw = cbor2.dumps([1, b"x" * 40])
    assert block_forge.item_end(raw) == len(raw)
    with pytest.raises(block_forge.Incomplete):
        block_forge.item_end(raw[:-3])
