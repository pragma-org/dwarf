"""F142: chain-sync / block-fetch violations are injected for real by replaying earlier genuine messages.

The six weak modes flipped one byte of the first segment of any protocol and wrote the semantic
fields (slot / height delta, served blocks, chain advanced) as constants. Now the proxy replays an
earlier, genuinely signed header (or block) and measures what it injected.
"""
from __future__ import annotations

import hashlib

import cbor2

from scripts import byzantine_mux_proxy as proxy


def _roll_forward(block_no, slot, prev):
    header = [[block_no, slot, prev, b"issuer", b"vrf"], b"kes-signature"]
    header_bytes = cbor2.dumps(header)
    wrapped = [6, cbor2.CBORTag(24, header_bytes)]
    payload = cbor2.dumps([2, wrapped, [[slot, b"\x00" * 32], block_no]])
    return payload, hashlib.blake2b(header_bytes, digest_size=32).digest()


def _segment(payload, protocol, responder=True):
    word = protocol | (proxy.RESPONDER_PROTOCOL_BIT if responder else 0)
    return (0).to_bytes(4, "big") + word.to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload


def _state(tmp_path, **kw):
    return proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=1, output_dir=tmp_path,
                            mutation_mode="replay_earlier", mutation_direction="inbound", **kw)


def test_decoder_reads_slot_block_and_parent():
    payload, h = _roll_forward(7, 700, b"\x11" * 32)
    info = proxy.decode_roll_forward(payload)
    assert (info["block_no"], info["slot"], info["prev_hash"]) == (7, 700, (b"\x11" * 32).hex())
    assert info["header_hash"] == h.hex()


def test_replaying_the_oldest_header_injects_a_measured_violation(tmp_path):
    state = _state(tmp_path, mutation_protocol="2", mutation_message="roll_forward", mutate_after_segments=3)
    record = state.note_connection()
    prev = b"\x00" * 32
    sent = []
    for i in range(4):
        payload, h = _roll_forward(10 + i, 100 + 10 * i, prev)
        out, mutated = proxy._mutate_segment(_segment(payload, 2), state=state, direction="inbound", record=record)
        sent.append((out, mutated))
        prev = h
    assert [m for _, m in sent] == [False, False, False, True]
    replayed = proxy.decode_roll_forward(sent[3][0][8:])
    assert (replayed["block_no"], replayed["slot"]) == (10, 100)
    [replay] = state.stats["replays"]
    assert replay["slot_delta"] == 100 - 120 and replay["height_delta"] == 10 - 12
    assert replay["parent_mismatch"] is True
    assert record["mutated_segments"] == 1


def test_replay_happens_once(tmp_path):
    state = _state(tmp_path, mutation_protocol="2", mutation_message="roll_forward", mutate_after_segments=2)
    record = state.note_connection()
    prev = b"\x00" * 32
    flags = []
    for i in range(6):
        payload, h = _roll_forward(i, i * 10, prev)
        flags.append(proxy._mutate_segment(_segment(payload, 2), state=state, direction="inbound", record=record)[1])
        prev = h
    assert flags.count(True) == 1


def test_corrupted_range_request_records_the_upstream_answer(tmp_path):
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=1, output_dir=tmp_path,
                             mutation_mode="flip_payload_byte", mutation_direction="outbound",
                             mutation_protocol="3", mutation_message="request_range", mutate_after_segments=1)
    record = state.note_connection()
    request = cbor2.dumps([0, [1, b"\x22" * 32], [2, b"\x33" * 32]])
    out, mutated = proxy._mutate_segment(_segment(request, 3, responder=False), state=state, direction="outbound", record=record)
    assert mutated
    proxy._mutate_segment(_segment(cbor2.dumps([3]), 3), state=state, direction="inbound", record=record)
    assert record["range_response"] == "no_blocks" and record["blocks_after_bad_range"] == 0


from scripts import runtime_chainsync_blockfetch_fault as fault

REPLAY = {"message_kind": "roll_forward", "slot_delta": -20, "height_delta": -2, "parent_mismatch": True}
DROPPED = {"id": 1, "mutated_segments": 1, "closed_by": "downstream", "drop_seconds": 0.3, "closed_at": 1.0}


def test_chainsync_modes_replay_earlier_headers():
    for mode in ("chainsync_parent_discontinuity", "chainsync_nonincrementing_height", "chainsync_nonmonotonic_slot"):
        d = fault.MODE_DEFAULTS[mode]
        assert (d["mutation_mode"], d["mutation_protocol"], d["mutation_message"], d["mutation_direction"]) == (
            "replay_earlier", "2", "roll_forward", "inbound")


def test_chainsync_result_is_measured():
    body = fault.build_result_body(mode="chainsync_nonmonotonic_slot", target_healthy=True,
                                   stats={"replays": [REPLAY], "connections": [DROPPED]})
    assert body["rejected"] is True and body["observed_slot_delta"] == -20 and body["observed_height_delta"] == -2
    assert body["parent_mismatch"] is True and body["candidate_chain_advanced"] is False and body["violation_crafted"] is True


def test_no_replay_is_not_a_rejection():
    body = fault.build_result_body(mode="chainsync_parent_discontinuity", target_healthy=True,
                                   stats={"intercepted_segments": 40, "connections_seen": 2})
    assert body["rejected"] is False and body["violation_crafted"] is False
    assert body["observed_slot_delta"] is None and body["candidate_chain_advanced"] is None


def test_block_replay_modes():
    for mode in ("blockfetch_range_mismatch", "blockfetch_continuity_failure"):
        d = fault.MODE_DEFAULTS[mode]
        assert (d["mutation_mode"], d["mutation_message"]) == ("replay_earlier", "block")
    body = fault.build_result_body(mode="blockfetch_range_mismatch", target_healthy=True,
                                   stats={"replays": [{"message_kind": "block", "block_mismatch": True}], "connections": [DROPPED]})
    assert body["range_mismatch_rejected"] is True and body["accepted_mismatched_range"] is False
    body = fault.build_result_body(mode="blockfetch_continuity_failure", target_healthy=True,
                                   stats={"replays": [{"message_kind": "block", "block_mismatch": True}], "connections": [DROPPED]})
    assert body["continuity_failure_rejected"] is True and body["downstream_state_advanced"] is False


def test_invalid_range_reads_the_upstream_answer():
    d = fault.MODE_DEFAULTS["blockfetch_invalid_range"]
    assert (d["mutation_direction"], d["mutation_message"]) == ("outbound", "request_range")
    conn = {"id": 1, "mutated_segments": 1, "range_request_corrupted": True, "range_response": "no_blocks", "blocks_after_bad_range": 0}
    body = fault.build_result_body(mode="blockfetch_invalid_range", target_healthy=True, stats={"connections": [conn]})
    assert body["invalid_range_rejected"] is True and body["served_blocks"] == 0
    served = {**conn, "range_response": "start_batch", "blocks_after_bad_range": 3}
    body = fault.build_result_body(mode="blockfetch_invalid_range", target_healthy=True, stats={"connections": [served]})
    assert body["invalid_range_rejected"] is False and body["served_blocks"] == 3


def test_ready_waits_for_the_injection_outcome():
    assert not fault.proxy_activity_ready(mode="chainsync_nonmonotonic_slot", stats={"intercepted_segments": 9})
    assert fault.proxy_activity_ready(mode="chainsync_nonmonotonic_slot", stats={"replays": [REPLAY], "connections": [DROPPED]})
    assert fault.proxy_activity_ready(mode="blockfetch_invalid_range",
                                      stats={"connections": [{"range_request_corrupted": True, "range_response": "no_blocks"}]})
